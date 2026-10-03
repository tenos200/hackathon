"""Constructors that derive IDs and hashes consistently for every adapter.

New biological assertions and supports always start `pending`; acceptance is
recorded only by importing an actual human decision (`atlas.review`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from atlas.hashing import sha256_text
from atlas.models.records import (
    Assertion, CoverageRecord, Entity, Evidence, Mapping, RecordField, Scope, Section, SourceDocument, Span, Support,
)
from atlas.sources.canonical import locate_quote, verify_span

PUBLIC_METADATA_KEYS = ("server", "doi", "manuscript_version", "license", "published_doi",
                        "publication_family_id", "fetch_backend", "original_target_url")


@dataclass
class AdapterOutput:
    documents: list[SourceDocument] = field(default_factory=list)
    entities: list[Entity] = field(default_factory=list)
    mappings: list[Mapping] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    assertions: list[Assertion] = field(default_factory=list)
    supports: list[Support] = field(default_factory=list)
    coverage: list[CoverageRecord] = field(default_factory=list)

    def extend(self, other: "AdapterOutput") -> None:
        for name in ("documents", "entities", "mappings", "evidence", "assertions", "supports", "coverage"):
            getattr(self, name).extend(getattr(other, name))


def source_metadata(**values: Any) -> dict[str, Any]:
    """Public metadata keys are always present (null when unknown); extra keys stay private."""
    meta = {key: None for key in PUBLIC_METADATA_KEYS}
    meta.update(values)
    return meta


def make_document(*, source_kind: str, external_ref: str, title: str, url: str | None, release_or_version: str | None,
                  published_at: str | None, fetched_at: str, raw_sha256: str, canonical_text: str,
                  canonicalizer_version: str, sections: list[Section], public_text_policy: str, origin: str,
                  metadata: dict[str, Any]) -> SourceDocument:
    canonical_sha = sha256_text(canonical_text)
    return SourceDocument(
        id=SourceDocument.make_id(external_ref, canonical_sha, canonicalizer_version), source_kind=source_kind,
        external_ref=external_ref, title=title, url=url, release_or_version=release_or_version,
        published_at=published_at, fetched_at=fetched_at, raw_sha256=raw_sha256, canonical_text=canonical_text,
        canonical_sha256=canonical_sha, canonicalizer_version=canonicalizer_version, sections=sections,
        public_text_policy=public_text_policy, origin=origin, source_metadata=metadata,
    )


def text_evidence(document: SourceDocument, quotes: list[tuple[str, str | None]], *, study_design: str,
                  species: str | None = None, patient_count: int | None = None,
                  study_or_cohort_ids: list[str] | None = None, independence: str = "unknown",
                  publication_status: str | None = None, source_native_validity: str | None = None,
                  extra_metadata: dict[str, Any] | None = None) -> Evidence:
    """Exact quotes located in the immutable canonical text (raises SpanError if not placeable)."""
    spans = sorted((locate_quote(document.canonical_text, q, document.sections, label) for q, label in quotes),
                   key=lambda s: (s.start, s.end))
    return text_evidence_from_spans(document, spans, study_design=study_design, species=species,
                                    patient_count=patient_count, study_or_cohort_ids=study_or_cohort_ids,
                                    independence=independence, publication_status=publication_status,
                                    source_native_validity=source_native_validity, extra_metadata=extra_metadata)


def text_evidence_from_spans(document: SourceDocument, spans: list[Span], *, study_design: str,
                             species: str | None = None, patient_count: int | None = None,
                             study_or_cohort_ids: list[str] | None = None, independence: str = "unknown",
                             publication_status: str | None = None,
                             source_native_validity: str | None = None,
                             extra_metadata: dict[str, Any] | None = None) -> Evidence:
    for span in spans:
        verify_span(document.canonical_text, span)
    status = publication_status or document.source_metadata.get("publication_status") or "unknown"
    return Evidence(
        id=Evidence.make_id(document.id, "text_spans", spans, None), source_document_id=document.id,
        kind="text_spans", spans=spans, record_locator=None, record_payload=None, study_design=study_design,
        species=species, patient_count=patient_count, study_or_cohort_ids=sorted(study_or_cohort_ids or []),
        independence=independence, publication_status=status, source_native_validity=source_native_validity,
        source_metadata=extra_metadata or {},
    )


def structured_evidence(document: SourceDocument, locator: str, fields: list[tuple[str, str]], *,
                        study_design: str, species: str | None = None,
                        source_native_validity: str | None = None, publication_status: str = "unknown",
                        extra_metadata: dict[str, Any] | None = None) -> Evidence:
    return Evidence(
        id=Evidence.make_id(document.id, "structured_record", [], locator), source_document_id=document.id,
        kind="structured_record", spans=[], record_locator=locator,
        record_payload=[RecordField(name=n, value=v) for n, v in fields], study_design=study_design,
        species=species, patient_count=None, study_or_cohort_ids=[], independence="unknown",
        publication_status=publication_status, source_native_validity=source_native_validity,
        source_metadata=extra_metadata or {},
    )


def make_assertion(*, subject_id: str, predicate: str, object_id: str, context_id: str | None, scope: Scope,
                   effect_direction: str, statement_status: str, origin: str, support_ids: list[str]) -> Assertion:
    return Assertion(
        id=Assertion.make_id(subject_id, predicate, object_id, context_id, scope, effect_direction, statement_status),
        subject_id=subject_id, predicate=predicate, object_id=object_id, context_id=context_id, scope=scope,
        effect_direction=effect_direction, statement_status=statement_status, origin=origin,
        support_ids=sorted(set(support_ids)), review_state="pending", review_id=None,
    )


def make_support(assertion_id: str, evidence_id: str, stance: str = "supports",
                 checker_result_id: str | None = None) -> Support:
    return Support(id=Support.make_id(assertion_id, evidence_id, stance), assertion_id=assertion_id,
                   evidence_id=evidence_id, stance=stance, checker_result_id=checker_result_id,
                   review_state="pending", review_id=None)


def claim(output: AdapterOutput, *, subject_id: str, predicate: str, object_id: str, context_id: str | None,
          scope: Scope, effect_direction: str, statement_status: str, origin: str, evidence: Evidence,
          stance: str = "supports") -> Assertion:
    """Append one evidence-backed pending assertion (exact duplicates merge support lists)."""
    assertion_id = Assertion.make_id(subject_id, predicate, object_id, context_id, scope, effect_direction,
                                     statement_status)
    support = make_support(assertion_id, evidence.id, stance)
    if evidence.id not in {e.id for e in output.evidence}:
        output.evidence.append(evidence)
    if support.id not in {s.id for s in output.supports}:
        output.supports.append(support)
    for index, existing in enumerate(output.assertions):
        if existing.id == assertion_id:
            merged = existing.model_copy(update={"support_ids": sorted({*existing.support_ids, support.id})})
            output.assertions[index] = Assertion.model_validate(merged.model_dump())
            return output.assertions[index]
    created = make_assertion(subject_id=subject_id, predicate=predicate, object_id=object_id, context_id=context_id,
                             scope=scope, effect_direction=effect_direction, statement_status=statement_status,
                             origin=origin, support_ids=[support.id])
    output.assertions.append(created)
    return created
