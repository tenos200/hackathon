"""The canonical snapshot package: what is hashed, stored and served.

`content` holds only published records and permitted source context. The
snapshot ID is `snap_<sha256(canonical JSON of content)>`; the `snapshot_id`
field itself is outside the hashed content. Operational timestamps (fetch,
review, insert times) are not part of content; `release.published_at` is the
human-set release date from config so that identical inputs reproduce the
same ID. Private review records, raw caches, model responses and full private
source texts never enter a package.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from atlas.hashing import canonical_json, content_sha256
from atlas.models.records import (
    Assertion, ComparisonRecord, Calculation, Conflict, ContextWindow, CoverageRecord, Entity, Explanation, GapRecord, Mapping,
    NonEmpty, OpportunityRecord, PublishedReview, PublicSourceView, Record, ResearchContext, Span, Support,
)

PACKAGE_FORMAT = "atlas-snapshot-package/1"


class PublicEvidence(Record):
    """Evidence as published: highlights only where the source's text policy permits."""

    id: NonEmpty
    source_id: NonEmpty
    kind: Literal["text_spans", "structured_record"]
    spans: list[Span]
    record_locator: str | None
    record_fields: list[tuple[str, str]]
    study_design: NonEmpty
    species: str | None
    patient_count: int | None = Field(ge=0)
    study_or_cohort_ids: list[str]
    independence: Literal["confirmed", "shared_data", "unknown"]
    publication_status: Literal["published", "preprint", "unknown"]
    source_native_validity: str | None
    metadata: dict[str, str | None]


class PublishedAssertion(Record):
    record: Assertion
    review: PublishedReview

    @model_validator(mode="after")
    def _check(self) -> "PublishedAssertion":
        if self.record.review_state != "accepted" or self.record.review_id is not None:
            raise ValueError("published assertions are accepted and carry no private review ID")
        if self.review.target_id != self.record.id:
            raise ValueError("review summary must belong to the assertion")
        return self


class ProfileTermSnapshot(Record):
    id: NonEmpty
    label: NonEmpty
    assertion_ids: list[str]


class ProfileSnapshot(Record):
    """Direct (observed) terms only; ancestors never count as observations."""

    mapped_annotation_ids: list[str]
    terms: list[ProfileTermSnapshot]
    direct_term_count: int = Field(ge=0)
    distinct_publication_count: int = Field(ge=0)
    reference_assessed_count: int = Field(ge=0)
    unassessed_term_ids: list[str]
    unresolved_disagreement_term_ids: list[str]


class PublishedContext(Record):
    record: ResearchContext
    review: PublishedReview
    audit: dict[str, Any]           # the hour-one context/mapping audit row for this context
    disease_profile: ProfileSnapshot
    subgroup_profile: ProfileSnapshot | None


class Release(Record):
    published_at: NonEmpty
    title: NonEmpty
    algorithm_version: NonEmpty
    limitations: list[str]
    example_context_ids: list[str]
    source_versions: list[tuple[str, str]]
    corpus_counts: dict[str, int]


class PackageContent(Record):
    release: Release
    entities: list[Entity]
    contexts: list[PublishedContext]
    mappings: list[Mapping]
    sources: list[PublicSourceView]
    evidence: list[PublicEvidence]
    assertions: list[PublishedAssertion]
    supports: list[Support]
    conflicts: list[Conflict]
    calculations: list[Calculation]
    comparisons: list[ComparisonRecord]
    opportunities: list[OpportunityRecord]
    gaps: list[GapRecord]
    coverage: list[CoverageRecord]
    explanations: list[Explanation]


# Table name -> (record key used for the row ID, foreign-key columns extracted from the row)
TABLES: dict[str, tuple[str, dict[str, str]]] = {
    "entities": ("id", {}),
    "contexts": ("record.id", {"disease_id": "record.disease_id"}),
    "mappings": ("id", {"source_id": "source_id"}),
    "sources": ("id", {}),
    "evidence": ("id", {"source_id": "source_id"}),
    "assertions": ("record.id", {"subject_id": "record.subject_id", "object_id": "record.object_id",
                                 "context_id": "record.context_id"}),
    "supports": ("id", {"assertion_id": "assertion_id", "evidence_id": "evidence_id"}),
    "conflicts": ("id", {}),
    "calculations": ("id", {}),
    "comparisons": ("id", {"context_a": "context_a", "context_b": "context_b"}),
    "opportunities": ("id", {"context_id": "starting_context_id", "asset_id": "asset_id"}),
    "gaps": ("id", {"context_id": "context_id"}),
    "coverage": ("id", {}),
    "explanations": ("id", {"context_id": "context_id"}),
}


def dotted(row: dict, path: str) -> Any:
    value: Any = row
    for part in path.split("."):
        value = value[part]
    return value


def canonical_content(content: PackageContent) -> dict[str, Any]:
    data = content.model_dump(mode="json")
    for table, (key, _) in TABLES.items():
        data[table] = sorted(data[table], key=lambda r: dotted(r, key))
    return data


# Operational timestamps that are served but excluded from the content hash.
OPERATIONAL_FIELDS = {"coverage": ("searched_at",)}


def hash_projection(content: dict[str, Any]) -> dict[str, Any]:
    """The one canonical projection used for hashing and idempotency comparisons.

    Timestamp-only differences (e.g. when a query was re-run) produce a new run
    manifest, not a different snapshot.
    """
    projected = dict(content)
    for table, fields in OPERATIONAL_FIELDS.items():
        projected[table] = [{k: v for k, v in row.items() if k not in fields} for row in content[table]]
    return projected


def snapshot_id_for(content: dict[str, Any]) -> str:
    return "snap_" + content_sha256(hash_projection(content))


def build_package(content: PackageContent) -> dict[str, Any]:
    canonical = canonical_content(content)
    return {"format": PACKAGE_FORMAT, "snapshot_id": snapshot_id_for(canonical), "content": canonical}


class PackageIntegrityError(ValueError):
    pass


def load_package(data: dict[str, Any], expected_snapshot_id: str | None = None) -> PackageContent:
    """Validate format, recompute the content hash and check every internal reference."""
    if data.get("format") != PACKAGE_FORMAT:
        raise PackageIntegrityError(f"unknown package format {data.get('format')!r}")
    content = PackageContent.model_validate_json(canonical_json(data["content"]))
    recomputed = snapshot_id_for(canonical_content(content))
    if recomputed != data.get("snapshot_id"):
        raise PackageIntegrityError(f"content hash {recomputed} does not match recorded {data.get('snapshot_id')}")
    if expected_snapshot_id is not None and recomputed != expected_snapshot_id:
        raise PackageIntegrityError(f"package is {recomputed}, not the pinned {expected_snapshot_id}")
    problems = reference_problems(content)
    if problems:
        raise PackageIntegrityError("; ".join(problems[:20]))
    return content


def reference_problems(content: PackageContent) -> list[str]:
    """Every ID used by a published record must resolve inside the same package."""
    problems: list[str] = []
    entities = {e.id for e in content.entities}
    contexts = {c.record.id for c in content.contexts}
    sources = {s.id: s for s in content.sources}
    evidence = {e.id: e for e in content.evidence}
    assertions = {a.record.id: a.record for a in content.assertions}
    supports = {s.id: s for s in content.supports}
    calculations = {c.id for c in content.calculations}
    comparisons = {c.id for c in content.comparisons}
    opportunities = {o.id for o in content.opportunities}
    coverage = {c.id for c in content.coverage}

    def need(ids, pool, what, owner):
        for i in ids:
            if i is not None and i not in pool:
                problems.append(f"{owner}: dangling {what} {i}")

    for table in TABLES:
        rows = content.model_dump(mode="json")[table]
        key = TABLES[table][0]
        ids = [dotted(r, key) for r in rows]
        if len(ids) != len(set(ids)):
            problems.append(f"{table}: duplicate IDs")
    for c in content.contexts:
        ctx = c.record
        need([ctx.disease_id, *ctx.gene_ids, ctx.mechanism_id], entities, "entity", ctx.id)
        need(ctx.definition_assertion_ids, assertions, "definition assertion", ctx.id)
        if ctx.definition_review_state != "accepted" and (ctx.mechanism_id or ctx.profile_level == "subgroup"):
            problems.append(f"{ctx.id}: subgroup/mechanism context definition is not accepted")
    for m in content.mappings:
        need([m.source_id], entities, "entity", m.id)
    for e in content.evidence:
        source = sources.get(e.source_id)
        if source is None:
            problems.append(f"{e.id}: dangling source {e.source_id}")
            continue
        if e.spans and source.public_text_policy == "link_only":
            problems.append(f"{e.id}: link_only source text must not be redistributed")
        for span in e.spans:
            window = next((w for w in source.context_windows if w.start <= span.start and span.end <= w.end), None)
            if window is None or window.text[span.start - window.start:span.end - window.start] != span.quote:
                problems.append(f"{e.id}: highlight is not inside a verified permitted context window")
    for a in assertions.values():
        need([a.subject_id, a.object_id], entities, "entity", a.id)
        need([a.context_id], contexts, "context", a.id)
        need(a.support_ids, supports, "support", a.id)
        if not any(supports.get(s) and supports[s].stance == "supports" for s in a.support_ids):
            problems.append(f"{a.id}: no accepted supporting evidence")
    for s in supports.values():
        need([s.assertion_id], assertions, "assertion", s.id)
        need([s.evidence_id], evidence, "evidence", s.id)
    for calc in content.calculations:
        need(calc.input_assertion_ids, assertions, "assertion", calc.id)
        need(calc.input_evidence_ids, evidence, "evidence", calc.id)
        need(calc.reference_source_ids, sources, "reference source", calc.id)
    for comp in content.comparisons:
        need([comp.context_a, comp.context_b], contexts, "context", comp.id)
        need(comp.calculation_ids, calculations, "calculation", comp.id)
        for f in comp.mechanism_features:
            need(f.assertion_ids, assertions, "assertion", comp.id)
        for result in (comp.disease_baseline, comp.subgroup_comparison):
            need([result.calculation_id], calculations, "calculation", comp.id)
    for o in content.opportunities:
        need([o.starting_context_id], contexts, "context", o.id)
        need([o.asset_id, *o.partner_entity_ids], entities, "entity", o.id)
        need(o.basis_assertion_ids, assertions, "assertion", o.id)
        need(o.comparison_ids, comparisons, "comparison", o.id)
    for g in content.gaps:
        need([g.context_id], contexts, "context", g.id)
        need(g.coverage_record_ids, coverage, "coverage", g.id)
    for x in content.explanations:
        need([x.context_id], contexts, "context", x.id)
        for s in x.sentences:
            need(s.assertion_ids, assertions, "assertion", x.id)
            need(s.calculation_ids, calculations, "calculation", x.id)
            need(s.opportunity_ids, opportunities, "opportunity", x.id)
    for conflict in content.conflicts:
        need(conflict.assertion_ids, assertions, "assertion", conflict.id)
        need(conflict.evidence_ids, evidence, "evidence", conflict.id)
    for cid in content.release.example_context_ids:
        need([cid], contexts, "example context", "release")
    return problems


def window_for(source: PublicSourceView, start: int, end: int) -> ContextWindow | None:
    return next((w for w in source.context_windows if w.start <= start and end <= w.end), None)
