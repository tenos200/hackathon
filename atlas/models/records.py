"""Strict internal domain records (master plan section 5).

All stages validate JSONL records on read and write with these models:
unknown fields are rejected, no implicit coercion of strings to numbers or
booleans, optional values are explicit nulls. Records are immutable values.
Entity properties are validated by a schema selected by entity type.
These records are internal; `atlas.api.dto` defines the public projection.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from atlas.hashing import sha256_text, stable_id

# ---------------------------------------------------------------- primitives

_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$")


def _utc(value: str) -> str:
    if not _UTC_RE.match(value):
        raise ValueError(f"timestamp must be UTC ISO 8601 ending in Z: {value!r}")
    return value


UtcTimestamp = Annotated[str, AfterValidator(_utc)]
NonEmpty = Annotated[str, Field(min_length=1)]


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    def payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


EntityType = Literal["gene", "disease", "variant", "mechanism", "phenotype", "process", "organization", "asset", "study", "person"]
ReviewState = Literal["pending", "accepted", "rejected", "needs_context"]
FunctionalEffect = Literal["loss", "gain", "dominant_negative", "mixed", "context_dependent", "unknown"]
AssetKind = Literal["registry", "natural_history_resource", "protocol", "questionnaire", "model", "assay", "biobank", "intervention", "other"]
StatementStatus = Literal["reported_result", "proposed", "planned", "ongoing", "background", "negated", "inconclusive", "listed_record"]
EffectDirection = Literal["improves", "worsens", "no_detected_effect", "unknown", "not_applicable"]
Origin = Literal["literature_extraction", "source_adapter", "manual_import"]
AliasType = Literal["label", "exact_synonym", "broad_synonym", "narrow_synonym", "related_synonym", "alt_id",
                    "symbol", "previous_symbol", "alias_symbol", "manual_alias"]
# Alias types that may resolve identity directly when unambiguous. All others
# only retrieve candidates for contextual checking.
IDENTITY_ALIAS_TYPES = frozenset({"label", "exact_synonym", "alt_id", "symbol"})

# ---------------------------------------------------------------- entities


class Alias(Record):
    text: NonEmpty
    alias_type: AliasType
    source_document_id: str | None


class GeneProps(Record):
    symbol: NonEmpty
    hgnc_id: str | None
    gene_family: str | None


class DiseaseProps(Record):
    mondo_id: str | None
    definition: str | None


class VariantProps(Record):
    gene_id: NonEmpty
    hgvs: str | None
    clinvar_id: str | None


class MechanismProps(Record):
    gene_id: NonEmpty
    functional_effect: FunctionalEffect
    process_ids: list[str]
    cell_tissue_text: str | None
    assay_species_text: str | None
    definition_evidence_ids: list[str]


class PhenotypeProps(Record):
    hpo_id: NonEmpty
    is_obsolete: bool
    replaced_by: str | None


class ProcessProps(Record):
    go_id: str | None


class OrganizationProps(Record):
    official_url: str | None  # registries such as NIH RePORTER name institutions without a URL
    contact_url: str | None
    domain: str | None  # an attribute, never the identity


class AssetProps(Record):
    kind: AssetKind
    owner_organization_id: str | None
    url: str | None
    materials_url: str | None
    access_terms_text: str | None
    population_scope: NonEmpty
    outcome_measures: list[str]
    age_scope: str | None
    genotype_scope: str | None
    reuse_permission: Literal["explicit", "restricted", "unknown"]
    last_checked_at: UtcTimestamp


class StudyProps(Record):
    nct_id: str | None
    design: NonEmpty
    status_as_reported: NonEmpty
    status_date: str | None
    conditions: list[str]
    eligibility_reference: str | None


class PersonProps(Record):
    profile_url: NonEmpty  # verified institutional/profile URL; a name is never an identity key
    orcid: str | None
    organization_id: str | None


PROPERTY_MODELS: dict[str, type[Record]] = {
    "gene": GeneProps, "disease": DiseaseProps, "variant": VariantProps, "mechanism": MechanismProps,
    "phenotype": PhenotypeProps, "process": ProcessProps, "organization": OrganizationProps,
    "asset": AssetProps, "study": StudyProps, "person": PersonProps,
}

_ID_PREFIXES: dict[str, tuple[str, ...]] = {
    "gene": ("HGNC:", "gene:"), "disease": ("MONDO:", "disease:"), "phenotype": ("HP:",),
    "process": ("GO:", "process:"), "variant": ("ClinVar:", "variant:"), "mechanism": ("mech:",),
    "organization": ("org:",), "asset": ("asset:",), "study": ("NCT", "study:"), "person": ("person:", "ORCID:"),
}


class Entity(Record):
    id: NonEmpty
    type: EntityType
    label: NonEmpty
    aliases: list[Alias]
    external_ids: list[str]
    properties: dict[str, Any]
    identity_source_ids: list[str]

    @model_validator(mode="after")
    def _check(self) -> "Entity":
        if not self.id.startswith(_ID_PREFIXES[self.type]):
            raise ValueError(f"{self.type} entity ID {self.id!r} must start with one of {_ID_PREFIXES[self.type]}")
        PROPERTY_MODELS[self.type].model_validate(self.properties)
        if len(self.id) > 180:
            raise ValueError("IDs are limited to 180 characters")
        return self

    def props(self) -> Any:
        return PROPERTY_MODELS[self.type].model_validate(self.properties)


class Mapping(Record):
    id: NonEmpty
    source_id: NonEmpty
    target_id: NonEmpty
    relation: Literal["exact", "broad", "narrow", "related"]
    source_document_id: NonEmpty
    review_state: ReviewState

    @staticmethod
    def make_id(source_id: str, target_id: str, relation: str, source_document_id: str) -> str:
        return stable_id("map", [source_id, target_id, relation, source_document_id])


class ResearchContext(Record):
    """A browsing/research grouping; never a diagnosis or a new canonical disease."""

    id: NonEmpty
    gene_ids: list[str]
    disease_id: NonEmpty
    mechanism_id: str | None
    profile_level: Literal["disease", "subgroup"]
    label: NonEmpty
    scope: NonEmpty
    definition_evidence_ids: list[str]
    definition_assertion_ids: list[str]
    definition_review_state: ReviewState

    @model_validator(mode="after")
    def _check(self) -> "ResearchContext":
        if self.mechanism_id is not None and self.profile_level != "subgroup":
            raise ValueError("a context that selects a mechanism is a subgroup-level context")
        if self.mechanism_id is not None and not self.definition_assertion_ids:
            raise ValueError("a non-null mechanism requires definition assertions with accepted evidence")
        return self


# ---------------------------------------------------------------- sources & evidence

SourceKind = Literal["ontology", "gene_registry", "curation_database", "literature", "trial_registry",
                     "organization_website", "institutional_website"]


class Section(Record):
    label: NonEmpty
    start: int = Field(ge=0)
    end: int = Field(ge=0)


class SourceDocument(Record):
    id: NonEmpty
    source_kind: SourceKind
    external_ref: NonEmpty
    title: NonEmpty
    url: str | None
    release_or_version: str | None
    published_at: str | None
    fetched_at: UtcTimestamp
    raw_sha256: NonEmpty
    canonical_text: str
    canonical_sha256: NonEmpty
    canonicalizer_version: NonEmpty
    sections: list[Section]
    public_text_policy: Literal["full", "excerpt", "link_only"]
    origin: Literal["download", "api", "manual_import"]
    source_metadata: dict[str, Any]

    @staticmethod
    def make_id(external_ref: str, canonical_sha256: str, canonicalizer_version: str) -> str:
        return stable_id("src", [external_ref, canonical_sha256, canonicalizer_version])

    @model_validator(mode="after")
    def _check(self) -> "SourceDocument":
        if "\r" in self.canonical_text:
            raise ValueError("canonical text must have normalized line endings")
        if sha256_text(self.canonical_text) != self.canonical_sha256:
            raise ValueError("canonical_sha256 does not match canonical_text")
        if self.id != self.make_id(self.external_ref, self.canonical_sha256, self.canonicalizer_version):
            raise ValueError("source ID must derive from external_ref, content hash and canonicalizer version")
        previous_end = 0
        for section in self.sections:
            if not (previous_end <= section.start <= section.end <= len(self.canonical_text)):
                raise ValueError(f"section {section.label!r} bounds are invalid or overlapping")
            previous_end = section.end
        return self

    def section_for(self, start: int, end: int) -> Section | None:
        for section in self.sections:
            if section.start <= start and end <= section.end:
                return section
        return None


class Span(Record):
    """Zero-based, half-open Unicode code-point offsets into canonical_text."""

    start: int = Field(ge=0)
    end: int = Field(ge=0)
    quote: NonEmpty
    section_label: str | None

    @model_validator(mode="after")
    def _check(self) -> "Span":
        if self.end - self.start != len(self.quote):
            raise ValueError("span length must equal quote length in code points")
        return self


class RecordField(Record):
    name: NonEmpty
    value: str


class Evidence(Record):
    id: NonEmpty
    source_document_id: NonEmpty
    kind: Literal["text_spans", "structured_record"]
    spans: list[Span]
    record_locator: str | None
    record_payload: list[RecordField] | None
    study_design: NonEmpty
    species: str | None
    patient_count: int | None = Field(ge=0)
    study_or_cohort_ids: list[str]
    independence: Literal["confirmed", "shared_data", "unknown"]
    publication_status: Literal["published", "preprint", "unknown"]
    source_native_validity: str | None
    source_metadata: dict[str, Any]

    @staticmethod
    def make_id(source_document_id: str, kind: str, spans: list[Span], record_locator: str | None) -> str:
        return stable_id("ev", [source_document_id, kind, [s.payload() for s in spans], record_locator])

    @model_validator(mode="after")
    def _check(self) -> "Evidence":
        if self.kind == "text_spans" and (not self.spans or self.record_payload is not None):
            raise ValueError("text evidence needs spans and no record payload")
        if self.kind == "structured_record" and (self.record_locator is None or self.record_payload is None or self.spans):
            raise ValueError("structured evidence needs an exact record locator and original fields, no prose spans")
        if self.id != self.make_id(self.source_document_id, self.kind, self.spans, self.record_locator):
            raise ValueError("evidence ID must derive from its source, kind, spans and locator")
        return self


# ---------------------------------------------------------------- assertions


class Scope(Record):
    variant_ids: list[str]
    population_text: str | None
    age_text: str | None
    species: str | None
    cell_or_tissue: str | None
    assay_text: str | None
    outcome_text: str | None
    comparator_text: str | None
    timeframe_text: str | None
    other_qualifiers: list[str]

    @classmethod
    def empty(cls) -> "Scope":
        return cls(variant_ids=[], population_text=None, age_text=None, species=None, cell_or_tissue=None,
                   assay_text=None, outcome_text=None, comparator_text=None, timeframe_text=None, other_qualifiers=[])

    def text_qualifiers(self) -> list[tuple[str, str]]:
        fields = ["population_text", "age_text", "species", "cell_or_tissue", "assay_text", "outcome_text",
                  "comparator_text", "timeframe_text"]
        out = [(f, getattr(self, f)) for f in fields if getattr(self, f) is not None]
        out += [("other_qualifiers", q) for q in self.other_qualifiers]
        return out

    def identity(self) -> dict[str, Any]:
        data = self.payload()
        data["variant_ids"] = sorted(data["variant_ids"])
        data["other_qualifiers"] = sorted(data["other_qualifiers"])
        return data


Predicate = Literal[
    "gene_associated_with_disease", "variant_in_gene", "variant_associated_with_disease", "variant_has_effect",
    "disease_has_mechanism", "mechanism_causes_disease", "has_phenotype", "gene_involved_in_process",
    "mechanism_affects_process", "responds_to", "studies", "tests", "serves", "owns_or_runs",
    "asset_for_context", "professional_at", "works_on",
    # Contract-extension predicates (docs/CONTRACT_NOTES.md): source-record relations only.
    "mentions", "investigator_on", "funds",
]


class Assertion(Record):
    id: NonEmpty
    subject_id: NonEmpty
    predicate: Predicate
    object_id: NonEmpty
    context_id: str | None
    scope: Scope
    effect_direction: EffectDirection
    statement_status: StatementStatus
    origin: Origin
    support_ids: list[str]
    review_state: ReviewState
    review_id: str | None

    @staticmethod
    def make_id(subject_id: str, predicate: str, object_id: str, context_id: str | None, scope: Scope,
                effect_direction: str, statement_status: str) -> str:
        """Hash of the claim's full identity. Scope wording is not paraphrased for deduplication."""
        return stable_id("assert", {
            "subject_id": subject_id, "predicate": predicate, "object_id": object_id, "context_id": context_id,
            "scope": scope.identity(), "effect_direction": effect_direction, "statement_status": statement_status,
        })

    @model_validator(mode="after")
    def _check(self) -> "Assertion":
        expected = self.make_id(self.subject_id, self.predicate, self.object_id, self.context_id, self.scope,
                                self.effect_direction, self.statement_status)
        if self.id != expected:
            raise ValueError("assertion ID must be the hash of its subject/predicate/object/context/scope/direction/status")
        return self


class Support(Record):
    id: NonEmpty
    assertion_id: NonEmpty
    evidence_id: NonEmpty
    stance: Literal["supports", "opposes", "inconclusive"]
    checker_result_id: str | None
    review_state: ReviewState
    review_id: str | None

    @staticmethod
    def make_id(assertion_id: str, evidence_id: str, stance: str) -> str:
        return stable_id("sup", [assertion_id, evidence_id, stance])


DimensionVerdict = Literal["pass", "fail", "not_assessed"]


class ReviewDimensions(Record):
    source_fidelity: DimensionVerdict
    entity_identity: DimensionVerdict
    direction: DimensionVerdict
    statement_status: DimensionVerdict
    scope: DimensionVerdict
    applicability: DimensionVerdict
    expert_validation: bool          # true only for an actual qualified expert review
    compatibility_qualified_review: bool  # required for any reuse_confirmed opportunity
    model_check_disagreement: str | None  # required reason when overriding a failed model check


ReviewTargetType = Literal["assertion", "support", "context", "mapping", "conflict", "opportunity", "explanation",
                           "entity", "demo_packet"]


class Review(Record):
    """A private human decision bound to the reviewed content hash."""

    id: NonEmpty
    target_type: ReviewTargetType
    target_id: NonEmpty
    target_content_sha256: NonEmpty
    reviewer: NonEmpty
    reviewed_at: UtcTimestamp
    decision: Literal["accepted", "rejected", "needs_context"]
    reason: NonEmpty
    dimensions: ReviewDimensions


# ---------------------------------------------------------------- conflicts, calculations, actions


class Conflict(Record):
    id: NonEmpty
    assertion_ids: list[str]
    comparability: Literal["comparable", "different_context", "unclear"]
    reason: NonEmpty
    evidence_ids: list[str]
    review_state: ReviewState
    review_id: str | None


class TermCoverage(Record):
    direct_terms: int = Field(ge=0)
    assessed_terms: int = Field(ge=0)
    fraction: float | None
    unassessed_term_ids: list[str]


class SharedTermResult(Record):
    id: NonEmpty
    label: NonEmpty
    ic: float | None
    score_contribution: float | None
    direct_a: bool
    direct_b: bool
    input_assertion_ids: list[str]


class PhenotypeResult(Record):
    score: float | None
    calculation_id: str | None
    profile_level: Literal["disease_baseline", "subgroup"]
    same_parent_disease: bool
    direct_annotation_counts: dict[str, int]
    publication_counts: dict[str, int]
    mapped_annotation_ids: dict[str, list[str]]
    input_assertion_ids: dict[str, list[str]]
    reference_coverage: dict[str, TermCoverage]
    shared_terms: list[SharedTermResult]
    specific_shared_terms: list[str]
    missingness: list[str]
    warnings: list[dict[str, str]]


class Calculation(Record):
    id: NonEmpty
    kind: NonEmpty
    algorithm_version: NonEmpty
    parameters: dict[str, str]
    input_assertion_ids: list[str]
    input_evidence_ids: list[str]
    reference_source_ids: list[str]
    result: PhenotypeResult
    missingness: list[str]
    content_sha256: NonEmpty


class MechanismFeatureRecord(Record):
    dimension: NonEmpty
    comparison: Literal["same", "different", "unknown", "not_comparable"]
    assertion_ids: list[str]
    explanation: NonEmpty


class SentenceRecord(Record):
    text: NonEmpty
    assertion_ids: list[str]
    calculation_ids: list[str]
    opportunity_ids: list[str]

    @model_validator(mode="after")
    def _check(self) -> "SentenceRecord":
        if not (self.assertion_ids or self.calculation_ids or self.opportunity_ids):
            raise ValueError("a substantive sentence needs at least one dependency")
        return self


class ComparisonRecord(Record):
    id: NonEmpty
    context_a: NonEmpty
    context_b: NonEmpty
    disease_baseline: PhenotypeResult
    subgroup_comparison: PhenotypeResult
    ranking_basis: Literal["disease_baseline", "subgroup", "unranked"]
    mechanism_features: list[MechanismFeatureRecord]
    relevant_differences: list[str]
    unknowns: list[str]
    calculation_ids: list[str]
    summary: list[SentenceRecord]


class OpportunityRecord(Record):
    id: NonEmpty
    starting_context_id: NonEmpty
    partner_entity_ids: list[str]
    asset_id: NonEmpty
    kind: Literal["infrastructure", "measurement", "experimental_method", "biological_question"]
    readiness: Literal["discovered", "investigate_compatibility", "reuse_confirmed"]
    basis_assertion_ids: list[str]
    comparison_ids: list[str]
    known_differences: list[str]
    unknowns: list[str]
    expert_checks: list[str]
    action_text: NonEmpty
    outreach_draft: str | None
    contact_url: str | None
    review_state: ReviewState
    review_id: str | None


class CoverageRecord(Record):
    id: NonEmpty
    source: NonEmpty
    query_or_urls: list[str]
    searched_at: UtcTimestamp
    filters: dict[str, str]
    result_cap: int | None
    returned_count: int = Field(ge=0)
    inspected_count: int = Field(ge=0)
    completion: Literal["complete_for_query", "capped", "failed", "partial"]
    failure_reason: str | None

    @model_validator(mode="after")
    def _check(self) -> "CoverageRecord":
        if self.completion in ("failed", "partial", "capped") and not self.failure_reason:
            raise ValueError("incomplete coverage must state why")
        return self


GapKind = Literal["not_found_in_search", "source_unavailable", "incomplete_retrieval", "unresolved_identity",
                  "insufficient_context", "conflicting_evidence", "compatibility_unknown"]


class GapRecord(Record):
    id: NonEmpty
    context_id: NonEmpty
    kind: GapKind
    coverage_record_ids: list[str]
    description: NonEmpty
    evidence_needed: list[str]
    next_question: NonEmpty


# ---------------------------------------------------------------- candidates, checks, explanations


class CandidateClaim(Record):
    id: NonEmpty
    source_document_id: NonEmpty
    subject_mention: NonEmpty
    subject_type: EntityType
    object_mention: NonEmpty
    object_type: EntityType
    predicate: Predicate
    scope: Scope
    effect_direction: EffectDirection
    statement_status: StatementStatus
    spans: list[Span]
    linked_subject_id: str | None
    linked_object_id: str | None
    context_id: str | None
    resolution_state: Literal["linked", "ambiguous", "unlinked", "invalid"]
    reason: str | None
    model_response_id: NonEmpty


class CheckDimension(Record):
    verdict: Literal["pass", "fail", "insufficient_context"]
    reason: NonEmpty
    evidence_spans: list[Span]


class CheckerDimensions(Record):
    text_supports_assertion: CheckDimension
    relation_and_direction: CheckDimension
    status_and_attribution: CheckDimension
    scope_preserved: CheckDimension
    entities_correct: CheckDimension


class CheckerResult(Record):
    id: NonEmpty
    candidate_id: NonEmpty
    candidate_content_sha256: NonEmpty
    model_response_id: NonEmpty
    dimensions: CheckerDimensions

    def passed(self) -> bool:
        return all(getattr(self.dimensions, f).verdict == "pass" for f in CheckerDimensions.model_fields)


class Explanation(Record):
    id: NonEmpty
    context_id: NonEmpty
    sentences: list[SentenceRecord]
    dependency_hashes: dict[str, str]
    origin: Literal["template", "model_then_reviewed", "human_authored"]
    model_response_id: str | None
    prompt_version: str | None
    policy_version: NonEmpty
    review_state: ReviewState
    review_id: str | None


class ContextWindow(Record):
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    text: str

    @model_validator(mode="after")
    def _check(self) -> "ContextWindow":
        if len(self.text) != self.end - self.start:
            raise ValueError("window length must equal end - start")
        return self


class PublicSourceView(Record):
    """What enters the public `sources` table; private originals stay private."""

    id: NonEmpty
    title: NonEmpty
    url: str | None
    external_ref: NonEmpty
    source_kind: SourceKind
    release_or_version: str | None
    publication_status: Literal["published", "preprint", "unknown"]
    raw_sha256: NonEmpty
    canonical_sha256: NonEmpty
    canonicalizer_version: NonEmpty
    public_text_policy: Literal["full", "excerpt", "link_only"]
    metadata: dict[str, str | None]
    context_windows: list[ContextWindow]

    @model_validator(mode="after")
    def _check(self) -> "PublicSourceView":
        if self.public_text_policy == "link_only" and self.context_windows:
            raise ValueError("link_only sources redistribute no text")
        return self


class PublishedReview(Record):
    """Public review summary: no reviewer identity or private reasoning."""

    target_id: NonEmpty
    description: NonEmpty
    expert_validation: bool
    reviewed_content_sha256: NonEmpty


class MechanismContextImport(Record):
    """One line of data/imports/mechanism_contexts.jsonl (master plan stage 2)."""

    context: ResearchContext
    entities: list[Entity]
    assertions: list[Assertion]
    evidence: list[Evidence]
    supports: list[Support]
    reviews: list[Review]
