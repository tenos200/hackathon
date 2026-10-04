"""Stage 8: apply the publication rules and build the canonical snapshot package.

Publication rules (master plan section 6) implemented here:
1. entity and predicate/type checks pass (structural report);
2. every cited evidence record resolves to its exact source version and spans verify;
3. qualifiers are preserved (structural failures block; nothing is deleted);
4. the assertion has an accepted human review bound to its current fingerprint
   (the fingerprint covers its support set and evidence);
5. potential disagreements (same subject/predicate/object with a different
   status or direction) need an accepted Conflict disposition; otherwise the
   bucket is withheld rather than published unqualified.
Accepted stale reviews, dangling IDs, missing audits or unreviewed contexts fail
assembly with a list instead of silently dropping records.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from atlas.analytics import phenotype
from atlas.analytics.mechanism import MechanismView
from atlas.analytics.neighborhood import build_neighborhoods
from atlas.assembly.package import (
    PackageContent, ProfileSnapshot, ProfileTermSnapshot, PublicEvidence, PublishedAssertion, PublishedContext,
    Release, build_package, load_package,
)
from atlas.checking.structural import structural_report
from atlas.models.records import (
    Assertion, ContextWindow, Evidence, GapRecord, OpportunityRecord, PublicSourceView, PublishedReview, Review,
    SourceDocument,
)
from atlas.review.fingerprint import Catalog
from atlas.review.workflow import effective, fingerprint, latest_reviews
from atlas.sources.adapters import NON_POSITIVE_NATIVE_VALIDITY
from atlas.sources.builders import PUBLIC_METADATA_KEYS
from atlas.sources.hpo import HpoOntology, IcReference

POSITIVE_STATUSES = frozenset({"reported_result", "listed_record"})
# Polarity classes used only to *flag* records for human conflict disposition; code
# never decides whether two records actually contradict each other.
POLARITY = {"reported_result": "positive", "listed_record": "positive", "negated": "negative",
            "inconclusive": "inconclusive"}
DIRECTIONS = frozenset({"improves", "worsens", "no_detected_effect"})


def has_non_positive_validity(evidence: list) -> bool:
    """Limited/disputed/refuted source classifications never count as positive established evidence."""
    return any(e.source_native_validity in NON_POSITIVE_NATIVE_VALIDITY for e in evidence)


def potential_disagreement(members: list) -> bool:
    """Same subject/predicate/object with different polarity or effect direction needs a disposition.

    Planned, proposed, ongoing and background statements are not opposition.
    """
    polarities = {POLARITY[a.statement_status] for a in members if a.statement_status in POLARITY}
    directions = {a.effect_direction for a in members if a.effect_direction in DIRECTIONS}
    return len(polarities) > 1 or len(directions) > 1
AUDIT_FIELDS = ("context_id", "intended_population", "mondo_id", "source_supported_granularity",
                "exact_mapped_ids", "hpo_annotation_rows", "baseline_available", "justification_source_ids")


@dataclass
class _PendingContext:
    record: Any
    review: PublishedReview
    audit: dict[str, Any]


def _profile_snapshot(profile, reference: IcReference | None, labels: dict[str, str]) -> ProfileSnapshot:
    cov = phenotype.coverage(profile, reference)
    return ProfileSnapshot(
        mapped_annotation_ids=sorted(profile.mapped_annotation_ids),
        terms=[ProfileTermSnapshot(id=t, label=labels.get(t, t), assertion_ids=sorted(ids))
               for t, ids in sorted(profile.direct.items())],
        direct_term_count=len(profile.direct), distinct_publication_count=len(profile.publication_families),
        reference_assessed_count=cov.assessed_terms, unassessed_term_ids=cov.unassessed_term_ids,
        unresolved_disagreement_term_ids=sorted(profile.unresolved_disagreements))


class AssemblyError(RuntimeError):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("assembly failed:\n- " + "\n- ".join(problems))
        self.problems = problems


@dataclass
class AssemblyReport:
    published: dict[str, int] = field(default_factory=dict)
    withheld: dict[str, list[str]] = field(default_factory=dict)  # record ID -> reasons
    warnings: list[str] = field(default_factory=list)

    def withhold(self, record_id: str, reason: str) -> None:
        self.withheld.setdefault(record_id, []).append(reason)


@dataclass
class AssemblyInputs:
    catalog: Catalog
    reviews: list[Review]
    checks: dict
    ontology: HpoOntology
    reference: IcReference | None
    reference_source_ids: list[str]
    audit: dict[str, Any]
    release: dict[str, Any]
    coverage: list
    gaps: list[GapRecord]


def _review_summary(review: Review, target_id: str) -> PublishedReview:
    dims = review.dimensions
    if dims.expert_validation:
        description = "Accepted after review by a qualified expert as a faithful, appropriately scoped sourced statement."
    else:
        description = ("Accepted as a faithful sourced statement after source-fidelity review; "
                       "this is not expert scientific validation.")
    return PublishedReview(target_id=target_id, description=description, expert_validation=dims.expert_validation,
                           reviewed_content_sha256=review.target_content_sha256)


def _family(evidence: Evidence, source: SourceDocument | None) -> str:
    reference = evidence.source_metadata.get("reference")
    if reference:
        return f"ref:{reference}"
    if source is not None:
        return source.source_metadata.get("publication_family_id") or f"source:{source.external_ref}"
    return f"source:{evidence.source_document_id}"


def assemble(inputs: AssemblyInputs) -> tuple[dict[str, Any], AssemblyReport]:
    cat = inputs.catalog
    report = AssemblyReport()
    problems: list[str] = []
    latest = latest_reviews(inputs.reviews)

    def state(kind: str, target_id: str) -> str:
        return effective(cat, latest, kind, target_id).state

    stale, orphaned = [], []
    for (t, i), r in sorted(latest.items()):
        if r.decision != "accepted":
            continue
        current = fingerprint(cat, t, i)
        if current is None:
            orphaned.append(f"{t}:{i}")  # the reviewed record no longer exists; nothing depends on it
        elif current != r.target_content_sha256:
            stale.append(f"{t}:{i}")
    if orphaned:
        report.warnings.append("reviews for records no longer present: " + ", ".join(orphaned))
    if stale:
        problems.append("accepted reviews are stale after content/dependency changes; re-review before publishing: "
                        + ", ".join(stale))

    structural = structural_report(cat, inputs.ontology)

    # ---------------------------------------------------------- contexts and audit (T33)
    published_contexts: dict[str, PublishedContext] = {}
    for context in sorted(cat.contexts.values(), key=lambda c: c.id):
        audit = inputs.audit.get(context.id)
        if audit is None or any(k not in audit for k in AUDIT_FIELDS):
            problems.append(f"context {context.id} has no complete hour-one context/mapping audit entry")
            continue
        if audit["mondo_id"] != context.disease_id:
            problems.append(f"context {context.id}: audit Mondo ID {audit['mondo_id']} differs from {context.disease_id}")
            continue
        if not audit["justification_source_ids"]:
            problems.append(f"context {context.id}: audit lacks the sources justifying its disease granularity")
            continue
        # Context definitions (including profile level) are reviewed before any scoring.
        ctx_state = state("context", context.id)
        if ctx_state != "accepted":
            report.withhold(context.id, f"context definition review is {ctx_state}")
            continue
        review = latest[("context", context.id)]
        published_contexts[context.id] = _PendingContext(
            record=context.model_copy(update={"definition_review_state": "accepted"}),
            review=_review_summary(review, context.id),
            audit={k: audit[k] for k in AUDIT_FIELDS})  # auditor identities and notes stay private

    # ---------------------------------------------------------- assertions (rules 1-5)
    candidates: dict[str, Assertion] = {}
    for assertion_id, assertion in sorted(cat.assertions.items()):
        if assertion_id in structural:
            report.withhold(assertion_id, "structural: " + "; ".join(structural[assertion_id]))
            continue
        a_state = state("assertion", assertion_id)
        if a_state != "accepted":
            report.withhold(assertion_id, f"review state {a_state}")
            continue
        if assertion.context_id is not None and assertion.context_id not in published_contexts:
            report.withhold(assertion_id, f"context {assertion.context_id} is not published")
            continue
        candidates[assertion_id] = assertion

    accepted_conflicts = {cid: c for cid, c in cat.conflicts.items() if state("conflict", cid) == "accepted"}
    buckets: dict[tuple[str, str, str], list[Assertion]] = {}
    for a in candidates.values():
        buckets.setdefault((a.subject_id, a.predicate, a.object_id), []).append(a)
    for key, members in buckets.items():
        if not potential_disagreement(members):
            continue
        ids = {a.id for a in members}
        if not any(ids <= set(c.assertion_ids) for c in accepted_conflicts.values()):
            for a in members:
                report.withhold(a.id, "potential disagreement in its subject/predicate/object bucket has no accepted "
                                      "conflict disposition")
                candidates.pop(a.id, None)

    # Definition assertions of published mechanism/subgroup contexts must themselves be published.
    for context_id, pc in list(published_contexts.items()):
        missing = [a for a in pc.record.definition_assertion_ids if a not in candidates]
        if missing:
            problems.append(f"context {context_id}: definition assertions are not publishable: {', '.join(missing)}")

    # ---------------------------------------------------------- evidence, supports, sources
    supports_out, evidence_ids = [], set()
    for a in candidates.values():
        for sid in a.support_ids:
            support = cat.supports[sid]
            supports_out.append(support.model_copy(update={"review_state": "accepted", "review_id": None,
                                                           "checker_result_id": None}))
            evidence_ids.add(support.evidence_id)
    assertion_review = {aid: latest[("assertion", aid)] for aid in candidates}

    sources_used = {cat.evidence[e].source_document_id for e in evidence_ids}
    public_sources: dict[str, PublicSourceView] = {}
    windows: dict[str, dict[tuple[int, int], ContextWindow]] = {s: {} for s in sources_used}
    public_evidence: list[PublicEvidence] = []
    for eid in sorted(evidence_ids):
        ev = cat.evidence[eid]
        source = cat.sources[ev.source_document_id]
        spans = []
        if ev.kind == "text_spans" and source.public_text_policy != "link_only":
            for span in ev.spans:
                section = source.section_for(span.start, span.end)
                start, end = (section.start, section.end) if section else (0, len(source.canonical_text))
                windows[source.id][(start, end)] = ContextWindow(start=start, end=end,
                                                                 text=source.canonical_text[start:end])
                spans.append(span)
        public_evidence.append(PublicEvidence(
            id=ev.id, source_id=source.id, kind=ev.kind, spans=spans, record_locator=ev.record_locator,
            record_fields=[(f.name, f.value) for f in (ev.record_payload or [])], study_design=ev.study_design,
            species=ev.species, patient_count=ev.patient_count, study_or_cohort_ids=ev.study_or_cohort_ids,
            independence=ev.independence, publication_status=ev.publication_status,
            source_native_validity=ev.source_native_validity,
            metadata={k: (str(v) if v is not None else None) for k, v in ev.source_metadata.items()}))

    def public_source(source: SourceDocument, source_windows: list[ContextWindow]) -> PublicSourceView:
        meta = {k: source.source_metadata.get(k) for k in PUBLIC_METADATA_KEYS}
        return PublicSourceView(
            id=source.id, title=source.title, url=source.url, external_ref=source.external_ref,
            source_kind=source.source_kind, release_or_version=source.release_or_version,
            publication_status=source.source_metadata.get("publication_status") or "unknown",
            raw_sha256=source.raw_sha256, canonical_sha256=source.canonical_sha256,
            canonicalizer_version=source.canonicalizer_version, public_text_policy=source.public_text_policy,
            metadata={k: (str(v) if v is not None else None) for k, v in meta.items()},
            context_windows=sorted(source_windows, key=lambda w: (w.start, w.end)))

    for sid in sorted(sources_used | set(inputs.reference_source_ids)):
        source = cat.sources.get(sid)
        if source is None:
            problems.append(f"source {sid} is not captured")
            continue
        # Windows were cut from the private original; verify before publishing.
        for w in windows.get(sid, {}).values():
            if source.canonical_text[w.start:w.end] != w.text:
                problems.append(f"source {sid}: context window does not match the private original")
        public_sources[sid] = public_source(source, list(windows.get(sid, {}).values()))

    # ---------------------------------------------------------- analytic eligibility
    accepted_exact = {(m.source_id, m.target_id) for m in cat.mappings.values()
                      if m.relation == "exact" and state("mapping", m.id) == "accepted"}
    usable_conflict_ids = {a for c in accepted_conflicts.values() if c.comparability == "different_context"
                           for a in c.assertion_ids}
    unresolved_conflict_ids = {a for c in accepted_conflicts.values() if c.comparability != "different_context"
                               for a in c.assertion_ids}

    def evidence_of(a: Assertion, stance: str = "supports") -> list[Evidence]:
        return [cat.evidence[cat.supports[s].evidence_id] for s in a.support_ids if cat.supports[s].stance == stance]

    def analytic_input(a: Assertion) -> bool:
        if a.id in unresolved_conflict_ids:
            return False
        if evidence_of(a, "opposes") and a.id not in usable_conflict_ids:
            return False
        supporting = evidence_of(a)
        if has_non_positive_validity(supporting):
            return False
        if a.origin == "source_adapter" and a.predicate == "has_phenotype":
            # HPO disease rows count only through accepted exact Mondo mappings.
            return any((a.subject_id, e.source_metadata.get("mapped_annotation_id")) in accepted_exact for e in supporting)
        return True

    labels = {e.id: e.label for e in cat.entities.values()}
    contexts = [pc.record for pc in published_contexts.values()]
    disease_level = {c.id for c in contexts if c.profile_level == "disease"}

    def build_profile(context_id: str, disease_id: str, level: str, members: list[Assertion]) -> phenotype.Profile:
        profile = phenotype.Profile(context_id=context_id, disease_id=disease_id, level=level)
        negated: set[str] = set()
        families: dict[str, set[str]] = {}
        for a in members:
            if a.statement_status == "negated":
                negated.add(a.object_id)
                continue
            if a.statement_status not in POSITIVE_STATUSES or not analytic_input(a):
                continue
            profile.direct.setdefault(a.object_id, set()).add(a.id)
            for e in evidence_of(a):
                profile.evidence_ids.add(e.id)
                families.setdefault(_family(e, cat.sources.get(e.source_document_id)), set()).update(e.study_or_cohort_ids)
                mapped = e.source_metadata.get("mapped_annotation_id")
                if mapped:
                    profile.mapped_annotation_ids.add(mapped)
        profile.unresolved_disagreements = negated & set(profile.direct)
        profile.publication_families = phenotype.merge_shared_cohorts(families)
        return profile

    phenotype_assertions = [a for a in candidates.values() if a.predicate == "has_phenotype"]
    baseline: dict[str, phenotype.Profile] = {}
    subgroup: dict[str, phenotype.Profile] = {}
    for c in contexts:
        members = [a for a in phenotype_assertions if a.subject_id == c.disease_id and (
            (a.context_id is None and a.origin == "source_adapter")
            or (a.context_id in disease_level and published_contexts[a.context_id].record.disease_id == c.disease_id))]
        baseline[c.id] = build_profile(c.id, c.disease_id, "disease_baseline", members)
        if c.profile_level == "subgroup":
            subgroup[c.id] = build_profile(c.id, c.disease_id, "subgroup",
                                           [a for a in phenotype_assertions if a.context_id == c.id])
        else:
            subgroup[c.id] = phenotype.Profile(context_id=c.id, disease_id=c.disease_id, level="subgroup",
                                               available=False)

    mechanisms: dict[str, MechanismView] = {}
    for c in contexts:
        mech = cat.entities.get(c.mechanism_id) if c.mechanism_id else None
        procs: dict[str, tuple[str, ...]] = {}
        if mech is not None:
            for a in candidates.values():
                if (a.predicate == "mechanism_affects_process" and a.subject_id == mech.id
                        and a.statement_status in POSITIVE_STATUSES and analytic_input(a)):
                    procs[a.object_id] = tuple(sorted({*procs.get(a.object_id, ()), a.id}))
        mechanisms[c.id] = MechanismView(c.id, mech, tuple(sorted(c.definition_assertion_ids)), procs)

    hood = build_neighborhoods(contexts, baseline, subgroup, mechanisms, inputs.ontology, inputs.reference, labels,
                               inputs.reference_source_ids)
    comparison_ids = {c.id for c in hood.comparisons}
    final_contexts = {
        cid: PublishedContext(
            record=pc.record, review=pc.review, audit=pc.audit,
            disease_profile=_profile_snapshot(baseline[cid], inputs.reference, labels),
            subgroup_profile=_profile_snapshot(subgroup[cid], inputs.reference, labels)
            if subgroup[cid].available else None)
        for cid, pc in published_contexts.items()}

    # ---------------------------------------------------------- opportunities (T15)
    opportunities: list[OpportunityRecord] = []
    for oid, opp in sorted(cat.opportunities.items()):
        if state("opportunity", oid) != "accepted":
            report.withhold(oid, "opportunity review is not accepted")
            continue
        asset = cat.entities.get(opp.asset_id)
        reasons = []
        if asset is None or asset.type != "asset":
            reasons.append("asset does not resolve")
        if opp.starting_context_id not in published_contexts:
            reasons.append("starting context is not published")
        reasons += [f"basis assertion {a} is not published" for a in opp.basis_assertion_ids if a not in candidates]
        reasons += [f"comparison {c} is not in this snapshot" for c in opp.comparison_ids if c not in comparison_ids]
        if not opp.basis_assertion_ids:
            reasons.append("an opportunity needs reviewed source-backed basis assertions")
        if opp.readiness == "reuse_confirmed":
            props = asset.properties if asset else {}
            review = latest.get(("opportunity", oid))
            if props.get("reuse_permission") != "explicit" or not props.get("materials_url"):
                reasons.append("reuse_confirmed requires explicit access permission and a materials URL")
            if review is None or not review.dimensions.compatibility_qualified_review:
                reasons.append("reuse_confirmed requires a recorded qualified compatibility review")
        if reasons:
            problems.append(f"opportunity {oid}: " + "; ".join(reasons))
            continue
        opportunities.append(opp.model_copy(update={"review_state": "accepted", "review_id": None}))

    # ---------------------------------------------------------- gaps and coverage (T16)
    coverage_by_id = {c.id: c for c in inputs.coverage}
    gaps: list[GapRecord] = []
    for gap in inputs.gaps:
        missing = [c for c in gap.coverage_record_ids if c not in coverage_by_id]
        if missing:
            problems.append(f"gap {gap.id} cites unknown coverage records {missing}")
            continue
        incomplete = [coverage_by_id[c] for c in gap.coverage_record_ids
                      if coverage_by_id[c].completion != "complete_for_query"]
        if gap.kind == "not_found_in_search" and (incomplete or not gap.coverage_record_ids):
            problems.append(f"gap {gap.id}: incomplete or missing coverage cannot support not_found_in_search")
            continue
        if gap.context_id not in published_contexts:
            report.withhold(gap.id, "context not published")
            continue
        gaps.append(gap)

    # ---------------------------------------------------------- explanations (T17)
    explanations = []
    for xid, x in sorted(cat.explanations.items()):
        if state("explanation", xid) != "accepted" or x.context_id not in published_contexts:
            report.withhold(xid, "explanation is not accepted or its context is unpublished")
            continue
        current = {}
        for s in x.sentences:
            current |= {a: fingerprint(cat, "assertion", a) for a in s.assertion_ids}
            current |= {c: c for c in s.calculation_ids}
            current |= {o: fingerprint(cat, "opportunity", o) for o in s.opportunity_ids}
        if current != x.dependency_hashes:
            report.withhold(xid, "dependency hashes changed since review; using deterministic record templates")
            continue
        cited = {a for s in x.sentences for a in s.assertion_ids}
        if not cited <= set(candidates) or any(o not in {p.id for p in opportunities}
                                               for s in x.sentences for o in s.opportunity_ids):
            report.withhold(xid, "cites records that are not published; using deterministic record templates")
            continue
        explanations.append(x.model_copy(update={"review_state": "accepted", "review_id": None}))

    # ---------------------------------------------------------- entities and mappings
    needed: set[str] = set()
    for a in candidates.values():
        needed |= {a.subject_id, a.object_id, *a.scope.variant_ids}
    for c in contexts:
        needed |= {c.disease_id, *c.gene_ids, *([c.mechanism_id] if c.mechanism_id else [])}
    for o in opportunities:
        needed |= {o.asset_id, *o.partner_entity_ids}
    # Shared ancestor terms are labelled from the ontology inside calculations; only
    # directly asserted terms need entity records.
    missing_entities = sorted(e for e in needed if e not in cat.entities)
    if missing_entities:
        problems.append("referenced entities are missing from the catalog: " + ", ".join(missing_entities))
    mappings = [m.model_copy(update={"review_state": "accepted"}) for m in cat.mappings.values()
                if m.source_id in needed and state("mapping", m.id) == "accepted"]

    if problems:
        raise AssemblyError(problems)

    release_cfg = inputs.release
    examples = [c for c in release_cfg.get("example_context_ids", []) if c in published_contexts]
    release = Release(
        published_at=release_cfg["published_at"], title=release_cfg["title"],
        algorithm_version=phenotype.ALGORITHM_VERSION, limitations=release_cfg.get("limitations", []),
        example_context_ids=examples or sorted(published_contexts)[:5],
        source_versions=sorted({(s.title if s.source_kind in ("ontology", "curation_database") else s.source_kind,
                                 s.release_or_version or "unversioned") for s in
                                (cat.sources[i] for i in public_sources)}),
        corpus_counts={"documents": len(public_sources), "published_assertions": len(candidates),
                       "contexts": len(published_contexts), "withheld_records": len(report.withheld)},
    )
    content = PackageContent(
        release=release,
        entities=[cat.entities[e] for e in sorted(needed)],
        contexts=list(final_contexts.values()),
        mappings=mappings,
        sources=list(public_sources.values()),
        evidence=public_evidence,
        assertions=[PublishedAssertion(
            record=a.model_copy(update={"review_state": "accepted", "review_id": None}),
            review=_review_summary(assertion_review[a.id], a.id)) for a in candidates.values()],
        supports=supports_out,
        conflicts=[c.model_copy(update={"review_state": "accepted", "review_id": None})
                   for c in accepted_conflicts.values() if set(c.assertion_ids) <= set(candidates)],
        calculations=hood.calculations, comparisons=hood.comparisons, opportunities=opportunities,
        gaps=gaps, coverage=sorted(inputs.coverage, key=lambda c: c.id),
        explanations=explanations,
    )
    package = build_package(content)
    load_package(package)  # referential and window integrity before anything is written
    report.published = {k: len(v) for k, v in package["content"].items() if isinstance(v, list)}
    return package, report


def write_package(package: dict[str, Any], directory) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{package['snapshot_id']}.json"
    path.write_text(json.dumps(package, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return str(path)
