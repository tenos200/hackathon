"""Self-contained community, asset, study and person imports (master plan section 8).

Rows in `data/imports/{communities,assets,studies,people}.jsonl` are manually
researched selections whose URLs were captured through the approved-URL source
client. They are normalized into Entity/Assertion/Evidence/Support records:
remit, ownership and intended scope are distinct assertions with distinct
support. `verified_by`/`verified_on` record who researched the row; they are
import provenance, not publication acceptance, which still requires an
imported human review decision bound to the generated content hashes.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from atlas.models.records import (
    AssetKind, Entity, Record, ResearchContext, Scope, SourceDocument, UtcTimestamp,
)
from atlas.sources.builders import AdapterOutput, claim, make_document, source_metadata, structured_evidence, text_evidence
from atlas.sources.canonical import HTML_CANONICALIZER, MARKDOWN_CANONICALIZER, canonicalize_html, canonicalize_text, paragraph_sections
from atlas.sources.fetch import Capture


def web_page(capture: Capture, body: bytes, *, title: str, source_kind: str, public_text_policy: str) -> SourceDocument:
    """Canonical web capture with backend provenance (direct, brightdata or recorded import)."""
    decoded = body.decode("utf-8", errors="replace")
    if capture.transformation == "markdown":
        text, version = canonicalize_text(decoded), MARKDOWN_CANONICALIZER
    else:
        text, version = canonicalize_html(decoded), HTML_CANONICALIZER
    if not text.strip():
        raise ValueError(f"{capture.target_url}: capture has no text; not accepted as a document")
    return make_document(
        source_kind=source_kind, external_ref=capture.target_url, title=title, url=capture.target_url,
        release_or_version=None, published_at=None, fetched_at=capture.fetched_at, raw_sha256=capture.raw_sha256,
        canonical_text=text, canonicalizer_version=version, sections=paragraph_sections(text),
        public_text_policy=public_text_policy, origin="download",
        metadata=source_metadata(fetch_backend=capture.backend, original_target_url=capture.target_url,
                                 requested_transformation=capture.transformation, publication_status="unknown"),
    )


class Quote(Record):
    quote: str = Field(min_length=1)
    section_label: str | None


class EvidenceRef(Record):
    source_document_id: str
    quotes: list[Quote]
    record_locator: str | None
    record_fields: list[tuple[str, str]] | None

    @model_validator(mode="after")
    def _check(self) -> "EvidenceRef":
        if bool(self.quotes) == bool(self.record_locator):
            raise ValueError("evidence needs either exact quotes or a structured record locator")
        return self


class OrganizationImport(Record):
    id: str
    name: str
    official_url: str
    contact_url: str | None
    served_disease_ids: list[str]
    serves_evidence: list[EvidenceRef]
    verified_by: str
    verified_on: UtcTimestamp


class AssetImport(Record):
    id: str
    name: str
    kind: AssetKind
    owner_organization_id: str | None
    ownership_relation: Literal["owns", "runs"] | None
    ownership_evidence: list[EvidenceRef]
    url: str | None
    target_context_ids: list[str]
    scope_evidence: list[EvidenceRef]
    materials_url: str | None
    access_terms_text: str | None
    population_scope: str
    outcome_measures: list[str]
    age_scope: str | None
    genotype_scope: str | None
    reuse_permission: Literal["explicit", "restricted", "unknown"]
    last_checked_at: UtcTimestamp
    verified_by: str
    verified_on: UtcTimestamp

    @model_validator(mode="after")
    def _check(self) -> "AssetImport":
        if (self.owner_organization_id is None) != (self.ownership_relation is None):
            raise ValueError("owner and ownership relation are given together")
        if self.owner_organization_id and not self.ownership_evidence:
            raise ValueError("ownership needs its own source support; a website mention is not ownership")
        return self


class StudyImport(Record):
    id: str
    title: str
    design: str
    status_as_reported: str
    status_date: str | None
    conditions: list[str]
    disease_ids: list[str]
    eligibility_reference: str | None
    evidence: list[EvidenceRef]
    verified_by: str
    verified_on: UtcTimestamp


class PersonImport(Record):
    id: str
    name: str
    profile_url: str  # a name alone is never an identity key
    orcid: str | None
    organization_id: str | None
    affiliation_evidence: list[EvidenceRef]
    work_entity_ids: list[str]
    work_evidence: list[EvidenceRef]
    verified_by: str
    verified_on: UtcTimestamp


def _evidence(ref: EvidenceRef, documents: dict[str, SourceDocument], study_design: str,
              metadata: dict[str, str] | None = None):
    document = documents.get(ref.source_document_id)
    if document is None:
        raise KeyError(f"evidence cites {ref.source_document_id}, which was not captured by the source stage")
    if ref.record_locator:
        return structured_evidence(document, ref.record_locator, list(ref.record_fields or []), study_design=study_design,
                                   extra_metadata=metadata)
    return text_evidence(document, [(q.quote, q.section_label) for q in ref.quotes], study_design=study_design,
                         extra_metadata=metadata)


def normalize_imports(*, organizations: list[OrganizationImport], assets: list[AssetImport],
                      studies: list[StudyImport], people: list[PersonImport], documents: dict[str, SourceDocument],
                      contexts: dict[str, ResearchContext]) -> AdapterOutput:
    out = AdapterOutput()
    design = "organization/resource web page"
    for org in organizations:
        out.entities.append(Entity(
            id=org.id, type="organization", label=org.name, aliases=[], external_ids=[],
            properties={"official_url": org.official_url, "contact_url": org.contact_url, "domain": None},
            identity_source_ids=sorted({r.source_document_id for r in org.serves_evidence}),
        ))
        for disease_id in org.served_disease_ids:
            for ref in org.serves_evidence:
                claim(out, subject_id=org.id, predicate="serves", object_id=disease_id, context_id=None,
                      scope=Scope.empty(), effect_direction="not_applicable", statement_status="listed_record",
                      origin="manual_import", evidence=_evidence(ref, documents, design))
    for asset in assets:
        out.entities.append(Entity(
            id=asset.id, type="asset", label=asset.name, aliases=[], external_ids=[],
            properties={"kind": asset.kind, "owner_organization_id": asset.owner_organization_id, "url": asset.url,
                        "materials_url": asset.materials_url, "access_terms_text": asset.access_terms_text,
                        "population_scope": asset.population_scope, "outcome_measures": asset.outcome_measures,
                        "age_scope": asset.age_scope, "genotype_scope": asset.genotype_scope,
                        "reuse_permission": asset.reuse_permission, "last_checked_at": asset.last_checked_at},
            identity_source_ids=sorted({r.source_document_id for r in asset.scope_evidence + asset.ownership_evidence}),
        ))
        if asset.owner_organization_id:
            for ref in asset.ownership_evidence:
                # Ownership vs operation is recorded in the evidence's source metadata.
                claim(out, subject_id=asset.owner_organization_id, predicate="owns_or_runs", object_id=asset.id,
                      context_id=None, scope=Scope.empty(), effect_direction="not_applicable",
                      statement_status="listed_record", origin="manual_import",
                      evidence=_evidence(ref, documents, design, {"ownership_relation": asset.ownership_relation}))
        for context_id in asset.target_context_ids:
            context = contexts.get(context_id)
            if context is None:
                raise KeyError(f"asset {asset.id} targets unknown context {context_id}")
            target = context.mechanism_id or context.disease_id
            for ref in asset.scope_evidence:
                claim(out, subject_id=asset.id, predicate="asset_for_context", object_id=target, context_id=context_id,
                      scope=Scope(**{**Scope.empty().payload(), "population_text": asset.population_scope}),
                      effect_direction="not_applicable", statement_status="listed_record", origin="manual_import",
                      evidence=_evidence(ref, documents, design))
    for study in studies:
        out.entities.append(Entity(
            id=study.id, type="study", label=study.title, aliases=[], external_ids=[study.id] if study.id.startswith("NCT") else [],
            properties={"nct_id": study.id if study.id.startswith("NCT") else None, "design": study.design,
                        "status_as_reported": study.status_as_reported, "status_date": study.status_date,
                        "conditions": study.conditions, "eligibility_reference": study.eligibility_reference},
            identity_source_ids=sorted({r.source_document_id for r in study.evidence}),
        ))
        for disease_id in study.disease_ids:
            for ref in study.evidence:
                claim(out, subject_id=study.id, predicate="studies", object_id=disease_id, context_id=None,
                      scope=Scope.empty(), effect_direction="not_applicable", statement_status="listed_record",
                      origin="manual_import", evidence=_evidence(ref, documents, study.design))
    for person in people:
        out.entities.append(Entity(
            id=person.id, type="person", label=person.name, aliases=[], external_ids=[person.orcid] if person.orcid else [],
            properties={"profile_url": person.profile_url, "orcid": person.orcid, "organization_id": person.organization_id},
            identity_source_ids=sorted({r.source_document_id for r in person.affiliation_evidence}),
        ))
        if person.organization_id:
            for ref in person.affiliation_evidence:
                claim(out, subject_id=person.id, predicate="professional_at", object_id=person.organization_id,
                      context_id=None, scope=Scope.empty(), effect_direction="not_applicable",
                      statement_status="listed_record", origin="manual_import", evidence=_evidence(ref, documents, design))
        for entity_id in person.work_entity_ids:
            for ref in person.work_evidence:
                claim(out, subject_id=person.id, predicate="works_on", object_id=entity_id, context_id=None,
                      scope=Scope.empty(), effect_direction="not_applicable", statement_status="listed_record",
                      origin="manual_import", evidence=_evidence(ref, documents, design))
    return out
