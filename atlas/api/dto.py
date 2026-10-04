"""Public wire DTOs for contract 1.0.0.

These mirror `contracts/openapi.json` exactly: unknown fields are rejected and
required-but-nullable fields have no default, so they are always serialized.
They are deliberately separate from the internal domain records in
`atlas.models`; projection code decides what is public. Private review records
have no representation here at all.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CONTRACT_VERSION = "1.0.0"

ContractVersion = Literal["1.0.0"]
DataMode = Literal["real", "synthetic_fixture"]
EntityType = Literal[
    "gene", "disease", "variant", "mechanism", "phenotype", "process", "organization", "asset", "study", "person"
]
ContextProfileLevel = Literal["disease", "subgroup"]
ScoreProfileLevel = Literal["disease_baseline", "subgroup"]
Effect = Literal["loss", "gain", "dominant_negative", "mixed", "context_dependent", "unknown"]
StatementStatus = Literal[
    "reported_result", "proposed", "planned", "ongoing", "background", "negated", "inconclusive", "listed_record"
]
EffectDirection = Literal["improves", "worsens", "no_detected_effect", "unknown", "not_applicable"]
Origin = Literal["literature_extraction", "source_adapter", "manual_import"]
AssetKind = Literal[
    "registry", "natural_history_resource", "protocol", "questionnaire", "model", "assay", "biobank", "intervention", "other"
]
AccessStatus = Literal["unknown", "contact_owner", "available_with_terms"]
GapKind = Literal[
    "not_found_in_search", "source_unavailable", "incomplete_retrieval", "unresolved_identity",
    "insufficient_context", "conflicting_evidence", "compatibility_unknown",
]
Completion = Literal["complete_for_query", "capped", "failed", "partial"]


class Dto(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Warning(Dto):  # noqa: A001 - contract schema name
    code: str
    message: str


class EntityRef(Dto):
    id: str
    label: str


class Sentence(Dto):
    text: str
    assertion_ids: list[str]
    calculation_ids: list[str]
    opportunity_ids: list[str]


class ContextOption(Dto):
    id: str
    label: str
    profile_level: ContextProfileLevel
    mechanism_known: bool


class SearchMatch(Dto):
    id: str
    entity_type: EntityType
    label: str
    matched_alias: str | None
    ambiguous: bool
    contexts: list[ContextOption]


class SearchData(Dto):
    query: str
    matches: list[SearchMatch]


class SourceVersion(Dto):
    name: str
    version: str


class Counts(Dto):
    documents: int = Field(ge=0)
    published_assertions: int = Field(ge=0)
    contexts: int = Field(ge=0)


class Capabilities(Dto):
    live_ai: Literal[False]
    request_snapshot_pin: bool
    graph: bool


class MetaData(Dto):
    published_at: str
    content_sha256: str
    source_versions: list[SourceVersion]
    counts: Counts
    algorithm_version: str
    capabilities: Capabilities
    example_contexts: list[ContextOption]
    limitations: list[str]


class Mechanism(Dto):
    id: str
    label: str
    effect: Effect


class ProfileTerm(Dto):
    id: str
    label: str
    assertion_ids: list[str]


class Profile(Dto):
    mapped_annotation_ids: list[str]
    terms: list[ProfileTerm]
    direct_term_count: int = Field(ge=0)
    distinct_publication_count: int = Field(ge=0)
    reference_assessed_count: int = Field(ge=0)
    unassessed_term_ids: list[str]


class ContextData(Dto):
    id: str
    label: str
    profile_level: ContextProfileLevel
    gene_ids: list[str]
    disease: EntityRef
    mechanism: Mechanism | None
    scope_text: str
    definition_assertion_ids: list[str]
    summary: list[Sentence]
    disease_profile: Profile
    subgroup_profile: Profile | None
    limitations: list[str]


class Coverage(Dto):
    direct_terms: int = Field(ge=0)
    assessed_terms: int = Field(ge=0)
    fraction: float | None = Field(ge=0, le=1)
    unassessed_term_ids: list[str]


class SharedTerm(Dto):
    id: str
    label: str
    ic: float | None = Field(ge=0)
    score_contribution: float | None = Field(ge=0, le=1)
    direct_a: bool
    direct_b: bool
    input_assertion_ids: list[str]


class PairIds(Dto):
    a: list[str]
    b: list[str]


class PairCounts(Dto):
    a: int = Field(ge=0)
    b: int = Field(ge=0)


class PairCoverage(Dto):
    a: Coverage
    b: Coverage


class Score(Dto):
    mapped_annotation_ids: PairIds
    profile_level: ScoreProfileLevel
    score: float | None = Field(ge=0, le=1)
    calculation_id: str | None
    same_parent_disease: bool
    direct_term_counts: PairCounts
    publication_counts: PairCounts
    reference_coverage: PairCoverage
    input_assertion_ids: PairIds
    shared_terms: list[SharedTerm]
    specific_shared_term_ids: list[str]
    missingness: list[str]
    warnings: list[Warning]


class MechanismFeature(Dto):
    dimension: str
    comparison: Literal["same", "different", "unknown", "not_comparable"]
    assertion_ids: list[str]
    explanation: str


class Comparison(Dto):
    id: str
    context_a: EntityRef
    context_b: EntityRef
    disease_baseline: Score
    subgroup_comparison: Score
    ranking_basis: Literal["disease_baseline", "subgroup", "unranked"]
    mechanism_features: list[MechanismFeature]
    relevant_differences: list[str]
    unknowns: list[str]
    summary: list[Sentence]


class GraphNode(Dto):
    id: str
    label: str
    type: str


class GraphLink(Dto):
    id: str
    source: str
    target: str
    assertion_id: str | None
    calculation_id: str | None
    computed: bool


class Graph(Dto):
    nodes: list[GraphNode]
    links: list[GraphLink]


class ConnectionsData(Dto):
    context_id: str
    comparisons: list[Comparison]
    counterexamples: list[Sentence]
    graph: Graph | None


class SourceMetadata(Dto):
    server: str | None
    doi: str | None
    manuscript_version: str | None
    license: str | None
    published_doi: str | None
    publication_family_id: str | None
    fetch_backend: str | None
    original_target_url: str | None


class Source(Dto):
    id: str
    title: str
    url: str | None
    external_ref: str
    source_kind: str
    release_or_version: str | None
    publication_status: Literal["published", "preprint", "unknown"]
    canonical_sha256: str
    raw_sha256: str
    canonicalizer_version: str
    public_text_policy: Literal["full", "excerpt", "link_only"]
    metadata: SourceMetadata


class Highlight(Dto):
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    quote: str


class TextContext(Dto):
    text: str
    canonical_start: int = Field(ge=0)
    canonical_end: int = Field(ge=0)
    highlights: list[Highlight]


class RecordField(Dto):
    name: str
    value: str


class EvidenceView(Dto):
    id: str
    stance: Literal["supports", "opposes", "inconclusive"]
    kind: Literal["text_spans", "structured_record"]
    source: Source
    context: TextContext | None
    record_locator: str | None
    study_or_cohort_ids: list[str]
    independence: Literal["confirmed", "shared_data", "unknown"]
    record_fields: list[RecordField]
    study_design: str
    species: str | None
    patient_count: int | None = Field(ge=0)
    source_native_validity: str | None
    scope_text: str


class ConflictView(Dto):
    id: str
    comparability: Literal["comparable", "different_context", "unclear"]
    assertion_ids: list[str]
    description: str


class ReviewSummary(Dto):
    status: Literal["accepted"]
    description: str
    expert_validation: bool
    reviewed_content_sha256: str


class Badge(Dto):
    kind: str
    label: str


class AssertionData(Dto):
    id: str
    subject: EntityRef
    predicate: str
    object: EntityRef
    context_id: str | None
    scope_text: str
    statement_status: StatementStatus
    effect_direction: EffectDirection
    origin: Origin
    review: ReviewSummary
    badges: list[Badge]
    evidence: list[EvidenceView]
    conflicts: list[ConflictView]


class Parameter(Dto):
    name: str
    value: str


class CalculationData(Dto):
    id: str
    kind: str
    algorithm_version: str
    parameters: list[Parameter]
    input_assertion_ids: list[str]
    input_evidence_ids: list[str]
    reference_sources: list[Source]
    result: Score
    explanation: list[Sentence]
    limitations: list[str]


class Asset(Dto):
    id: str
    name: str
    kind: AssetKind
    owner: EntityRef | None
    url: str | None
    description: list[Sentence]
    assertion_ids: list[str]
    access_status: AccessStatus
    access_terms: str | None


class Partner(Dto):
    id: str
    label: str
    contact_url: str | None


class Opportunity(Dto):
    id: str
    kind: Literal["infrastructure", "measurement", "experimental_method", "biological_question"]
    readiness: Literal["discovered", "investigate_compatibility", "reuse_confirmed"]
    asset: Asset
    partners: list[Partner]
    basis_assertion_ids: list[str]
    comparison_ids: list[str]
    known_differences: list[str]
    unknowns: list[str]
    expert_checks: list[str]
    action: Sentence
    outreach_draft: str | None


class CoverageRecord(Dto):
    id: str
    source: str
    query_or_urls: list[str]
    searched_at: str
    returned_count: int = Field(ge=0)
    inspected_count: int = Field(ge=0)
    completion: Completion
    failure_reason: str | None


class Gap(Dto):
    id: str
    kind: GapKind
    description: str
    coverage: list[CoverageRecord]
    evidence_needed: list[str]
    next_question: str


class ActionsData(Dto):
    context_id: str
    exact_disease_assets: list[Asset]
    opportunities: list[Opportunity]
    gaps: list[Gap]


class ErrorBody(Dto):
    code: str
    message: str
    request_id: str
    retryable: bool


class ErrorEnvelope(Dto):
    contract_version: ContractVersion
    snapshot_id: str | None
    error: ErrorBody


class Health(Dto):
    status: Literal["ok", "ready", "not_ready"]
    contract_version: ContractVersion
    snapshot_id: str | None
    data_mode: DataMode | None


class _Envelope(Dto):
    contract_version: ContractVersion
    snapshot_id: str
    data_mode: DataMode
    warnings: list[Warning]


class MetaResponse(_Envelope):
    data: MetaData


class SearchResponse(_Envelope):
    data: SearchData


class ContextResponse(_Envelope):
    data: ContextData


class ConnectionsResponse(_Envelope):
    data: ConnectionsData


class AssertionResponse(_Envelope):
    data: AssertionData


class CalculationResponse(_Envelope):
    data: CalculationData


class ActionsResponse(_Envelope):
    data: ActionsData


# Names in the committed contract, used by drift tests.
CONTRACT_SCHEMA_MODELS: dict[str, type[Dto]] = {
    "Warning": Warning, "EntityRef": EntityRef, "Sentence": Sentence, "ContextOption": ContextOption,
    "SearchMatch": SearchMatch, "SearchData": SearchData, "MetaData": MetaData, "Mechanism": Mechanism,
    "ProfileTerm": ProfileTerm, "Profile": Profile, "ContextData": ContextData, "Coverage": Coverage,
    "SharedTerm": SharedTerm, "Score": Score, "MechanismFeature": MechanismFeature, "Comparison": Comparison,
    "Graph": Graph, "ConnectionsData": ConnectionsData, "Source": Source, "Highlight": Highlight,
    "TextContext": TextContext, "EvidenceView": EvidenceView, "ConflictView": ConflictView,
    "AssertionData": AssertionData, "CalculationData": CalculationData, "Asset": Asset, "Partner": Partner,
    "Opportunity": Opportunity, "CoverageRecord": CoverageRecord, "Gap": Gap, "ActionsData": ActionsData,
    "ErrorEnvelope": ErrorEnvelope, "Health": Health, "MetaResponse": MetaResponse,
    "SearchResponse": SearchResponse, "ContextResponse": ContextResponse,
    "ConnectionsResponse": ConnectionsResponse, "AssertionResponse": AssertionResponse,
    "CalculationResponse": CalculationResponse, "ActionsResponse": ActionsResponse,
}
