"""Human review queue, decision import, effective acceptance and catalog freeze.

The software never records acceptance on its own. `review-export` writes a
local queue (HTML table + JSONL template with null decisions); a person fills
in reviewer, time, decision, reason and dimensions; `review-import` validates
each line against the *current* content fingerprint. A record counts as
accepted only while its latest matching review is `accepted`; any change in
the record or its dependencies makes that review stale. Structural failures
cannot be overridden, and accepting against a failed model check requires a
recorded disagreement reason.
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass, field
from pathlib import Path

from atlas.hashing import canonical_json, content_sha256, stable_id
from atlas.models.records import CheckerResult, Review, ReviewDimensions
from atlas.review.fingerprint import Catalog

TARGET_TYPES = ("assertion", "support", "context", "mapping", "entity", "conflict", "opportunity", "explanation")


@dataclass
class QueueItem:
    target_type: str
    target_id: str
    target_content_sha256: str
    summary: str
    context_text: str
    structural_problems: list[str]
    model_check: str | None


@dataclass
class ImportReport:
    imported: list[Review] = field(default_factory=list)
    rejected: list[tuple[int, str]] = field(default_factory=list)


def fingerprint(catalog: Catalog, target_type: str, target_id: str) -> str | None:
    return {
        "assertion": catalog.fingerprint_assertion, "support": catalog.fingerprint_support,
        "context": catalog.fingerprint_context, "mapping": catalog.fingerprint_mapping,
        "entity": catalog.fingerprint_entity, "conflict": catalog.fingerprint_conflict,
        "opportunity": catalog.fingerprint_opportunity, "explanation": catalog.fingerprint_explanation,
    }[target_type](target_id)


def latest_reviews(reviews: list[Review]) -> dict[tuple[str, str], Review]:
    latest: dict[tuple[str, str], Review] = {}
    for review in sorted(reviews, key=lambda r: (r.reviewed_at, r.id)):
        latest[(review.target_type, review.target_id)] = review
    return latest


@dataclass(frozen=True)
class EffectiveReview:
    state: str                 # accepted | rejected | needs_context | pending
    review: Review | None
    stale: bool                # a review exists but no longer matches the current content


def effective(catalog: Catalog, reviews: dict[tuple[str, str], Review], target_type: str, target_id: str,
              current_hash: str | None = None) -> EffectiveReview:
    review = reviews.get((target_type, target_id))
    current = current_hash if current_hash is not None else fingerprint(catalog, target_type, target_id)
    if review is None:
        return EffectiveReview("pending", None, False)
    if current is None or review.target_content_sha256 != current:
        return EffectiveReview("pending", review, True)
    return EffectiveReview(review.decision, review, False)


# ---------------------------------------------------------------- export


def _assertion_item(catalog: Catalog, assertion_id: str, structural: dict[str, list[str]],
                    checks: dict[str, CheckerResult]) -> QueueItem:
    a = catalog.assertions[assertion_id]
    subject = catalog.entities.get(a.subject_id)
    obj = catalog.entities.get(a.object_id)
    quotes = []
    for sid in a.support_ids:
        support = catalog.supports.get(sid)
        evidence = catalog.evidence.get(support.evidence_id) if support else None
        if evidence is None:
            continue
        source = catalog.sources.get(evidence.source_document_id)
        if evidence.kind == "text_spans" and source:
            for span in evidence.spans:
                section = source.section_for(span.start, span.end)
                window = source.canonical_text[section.start:section.end] if section else source.canonical_text
                quotes.append(f"[{support.stance}] {source.external_ref} {section.label if section else ''}: "
                              f"«{span.quote}» in: {window}")
        else:
            fields = "; ".join(f"{f.name}={f.value}" for f in (evidence.record_payload or []))
            quotes.append(f"[{support.stance}] {source.external_ref if source else evidence.source_document_id} "
                          f"{evidence.record_locator}: {fields} (native validity: {evidence.source_native_validity})")
    check = checks.get(assertion_id)
    check_text = None
    if check is not None:
        check_text = "; ".join(f"{name}: {getattr(check.dimensions, name).verdict} — {getattr(check.dimensions, name).reason}"
                               for name in type(check.dimensions).model_fields)
    scope = ", ".join(f"{k}={v}" for k, v in a.scope.text_qualifiers()) or "no scope qualifiers"
    summary = (f"{subject.label if subject else a.subject_id} [{a.predicate}] {obj.label if obj else a.object_id} | "
               f"context={a.context_id} | status={a.statement_status} | direction={a.effect_direction} | "
               f"origin={a.origin} | scope: {scope}")
    return QueueItem("assertion", assertion_id, catalog.fingerprint_assertion(assertion_id) or "UNRESOLVABLE",
                     summary, "\n".join(quotes), structural.get(assertion_id, []), check_text)


def export_queue(catalog: Catalog, reviews: list[Review], structural: dict[str, list[str]],
                 checks: dict[str, CheckerResult], out_dir: Path) -> list[QueueItem]:
    """Write review/queue.html and review/decisions.template.jsonl for items needing a decision."""
    latest = latest_reviews(reviews)
    items: list[QueueItem] = []
    for assertion_id in sorted(catalog.assertions):
        if effective(catalog, latest, "assertion", assertion_id).state == "pending":
            items.append(_assertion_item(catalog, assertion_id, structural, checks))
    for context_id, context in sorted(catalog.contexts.items()):
        if effective(catalog, latest, "context", context_id).state == "pending":
            items.append(QueueItem("context", context_id, catalog.fingerprint_context(context_id) or "UNRESOLVABLE",
                                   f"{context.label} | level={context.profile_level} | disease={context.disease_id} | "
                                   f"mechanism={context.mechanism_id} | scope: {context.scope}",
                                   "definition assertions: " + ", ".join(context.definition_assertion_ids), [], None))
    for mapping_id, mapping in sorted(catalog.mappings.items()):
        if effective(catalog, latest, "mapping", mapping_id).state == "pending":
            items.append(QueueItem("mapping", mapping_id, catalog.fingerprint_mapping(mapping_id) or "UNRESOLVABLE",
                                   f"{mapping.source_id} {mapping.relation} {mapping.target_id}", "", [], None))
    for kind, pool in (("conflict", catalog.conflicts), ("opportunity", catalog.opportunities),
                       ("explanation", catalog.explanations)):
        for target_id, record in sorted(pool.items()):
            if effective(catalog, latest, kind, target_id).state == "pending":
                items.append(QueueItem(kind, target_id, fingerprint(catalog, kind, target_id) or "UNRESOLVABLE",
                                       canonical_json(record.payload()), "", [], None))
    out_dir.mkdir(parents=True, exist_ok=True)
    blank_dims = {k: "not_assessed" for k in ("source_fidelity", "entity_identity", "direction", "statement_status",
                                             "scope", "applicability")}
    blank_dims |= {"expert_validation": False, "compatibility_qualified_review": False, "model_check_disagreement": None}
    with (out_dir / "decisions.template.jsonl").open("w", encoding="utf-8") as handle:
        for item in items:
            handle.write(canonical_json({
                "target_type": item.target_type, "target_id": item.target_id,
                "target_content_sha256": item.target_content_sha256, "reviewer": None, "reviewed_at": None,
                "decision": None, "reason": None, "dimensions": blank_dims}) + "\n")
    rows = "".join(
        f"<tr><td>{html.escape(i.target_type)}</td><td><code>{html.escape(i.target_id)}</code></td>"
        f"<td>{html.escape(i.summary)}</td><td><pre>{html.escape(i.context_text)}</pre></td>"
        f"<td>{html.escape('; '.join(i.structural_problems) or 'none')}</td>"
        f"<td>{html.escape(i.model_check or 'no model check')}</td>"
        f"<td><code>{html.escape(i.target_content_sha256[:16])}</code></td></tr>"
        for i in items)
    (out_dir / "queue.html").write_text(
        "<!doctype html><meta charset=utf-8><title>Atlas review queue (private)</title>"
        "<style>body{font:14px sans-serif}td{vertical-align:top;border:1px solid #ccc;padding:4px}"
        "pre{white-space:pre-wrap;max-width:60ch}</style>"
        "<h1>Review queue</h1><p>Private. Accepted means a faithful sourced statement, not proof of biology. "
        "Record decisions in review/decisions.jsonl (copy lines from decisions.template.jsonl).</p>"
        "<table><tr><th>type</th><th>id</th><th>record</th><th>source context</th><th>structural problems</th>"
        f"<th>model check (aid only)</th><th>hash</th></tr>{rows}</table>", encoding="utf-8")
    return items


# ---------------------------------------------------------------- import


def import_decisions(path: Path, catalog: Catalog, structural: dict[str, list[str]],
                     checks: dict[str, CheckerResult]) -> ImportReport:
    report = ImportReport()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            report.rejected.append((number, "not valid JSON"))
            continue
        missing = [k for k in ("reviewer", "reviewed_at", "decision", "reason") if not row.get(k)]
        if missing:
            report.rejected.append((number, f"no human decision recorded ({', '.join(missing)} empty)"))
            continue
        if row.get("target_type") not in TARGET_TYPES:
            report.rejected.append((number, f"unsupported target_type {row.get('target_type')!r}"))
            continue
        current = fingerprint(catalog, row["target_type"], row["target_id"])
        if current is None:
            report.rejected.append((number, f"{row['target_type']} {row['target_id']} does not exist or has dangling dependencies"))
            continue
        if row.get("target_content_sha256") != current:
            report.rejected.append((number, "stale: the record or its dependencies changed since this item was exported"))
            continue
        try:
            dims = ReviewDimensions.model_validate(row["dimensions"])
        except Exception as exc:
            report.rejected.append((number, f"invalid dimensions: {exc}"))
            continue
        if row["decision"] == "accepted" and row["target_type"] == "assertion":
            problems = structural.get(row["target_id"], [])
            if problems:
                report.rejected.append((number, "structural failures cannot be overridden: " + "; ".join(problems)))
                continue
            check = checks.get(row["target_id"])
            if check is not None and not check.passed() and not dims.model_check_disagreement:
                report.rejected.append((number, "model check did not pass; accepting requires model_check_disagreement reason"))
                continue
        review_id = stable_id("review", [row["target_type"], row["target_id"], current, row["reviewer"],
                                         row["reviewed_at"], row["decision"]])
        try:
            report.imported.append(Review(
                id=review_id, target_type=row["target_type"], target_id=row["target_id"],
                target_content_sha256=current, reviewer=row["reviewer"], reviewed_at=row["reviewed_at"],
                decision=row["decision"], reason=row["reason"], dimensions=dims))
        except Exception as exc:
            report.rejected.append((number, f"invalid review: {exc}"))
    return report


# ---------------------------------------------------------------- catalog freeze


def catalog_manifest(catalog: Catalog) -> dict:
    entities = {e: catalog.entity_hash(e) for e in sorted(catalog.entities)}
    contexts = {c: catalog.context_hash(c) for c in sorted(catalog.contexts)}
    mappings = {m: catalog.fingerprint_mapping(m) for m in sorted(catalog.mappings)}
    body = {"entities": entities, "contexts": contexts, "mappings": mappings}
    return {**body, "catalog_sha256": content_sha256(body)}


def catalog_impact(catalog: Catalog, frozen: dict, reviews: list[Review]) -> dict:
    """List catalog changes since the freeze and every review/record they affect.

    Appending an unrelated entity changes nothing that existing reviews depend on.
    """
    current = catalog_manifest(catalog)
    changed: set[str] = set()
    for kind in ("entities", "contexts", "mappings"):
        before, after = frozen.get(kind, {}), current[kind]
        changed |= {k for k in before if after.get(k) != before[k]}  # modified or removed
    affected_assertions = sorted(a for a in catalog.assertions if catalog.assertion_dependencies(a) & changed)
    latest = latest_reviews(reviews)
    stale_reviews = sorted(
        f"{t}:{i}" for (t, i), r in latest.items() if r.decision == "accepted"
        and fingerprint(catalog, t, i) not in (None, r.target_content_sha256))
    added = sorted(set(current["entities"]) - set(frozen.get("entities", {})))
    return {"frozen_catalog_sha256": frozen.get("catalog_sha256"), "current_catalog_sha256": current["catalog_sha256"],
            "changed_catalog_ids": sorted(changed), "appended_entity_ids": added,
            "affected_assertion_ids": affected_assertions, "stale_accepted_reviews": stale_reviews}


# ---------------------------------------------------------------- batch decisions by a person


def batch_targets(catalog: Catalog, reviews: list[Review], *, target_type: str, predicate: str | None = None,
                  origin: str | None = None, source_ref_contains: str | None = None, context_id: str | None = None,
                  ids: set[str] | None = None, include_decided: bool = False) -> list[tuple[str, str]]:
    """(target_id, current fingerprint) of records matching the filters, pending unless include_decided."""
    latest = latest_reviews(reviews)
    pool = {"assertion": catalog.assertions, "context": catalog.contexts, "mapping": catalog.mappings,
            "conflict": catalog.conflicts, "opportunity": catalog.opportunities,
            "explanation": catalog.explanations}[target_type]
    out = []
    for target_id, record in sorted(pool.items()):
        if ids is not None and target_id not in ids:
            continue
        if target_type == "assertion":
            if predicate and record.predicate != predicate:
                continue
            if origin and record.origin != origin:
                continue
            if context_id is not None and record.context_id != context_id:
                continue
            if source_ref_contains:
                refs = {catalog.sources[catalog.evidence[catalog.supports[s].evidence_id].source_document_id].external_ref
                        for s in record.support_ids if s in catalog.supports
                        and catalog.supports[s].evidence_id in catalog.evidence}
                if not any(source_ref_contains in r for r in refs):
                    continue
        if not include_decided and effective(catalog, latest, target_type, target_id).state != "pending":
            continue
        current = fingerprint(catalog, target_type, target_id)
        if current is not None:
            out.append((target_id, current))
    return out


def batch_decision_rows(targets: list[tuple[str, str]], *, target_type: str, reviewer: str, reviewed_at: str,
                        decision: str, reason: str, expert: bool, disagreement: str | None) -> list[dict]:
    dims = {k: "pass" if decision == "accepted" else "not_assessed"
            for k in ("source_fidelity", "entity_identity", "direction", "statement_status", "scope", "applicability")}
    dims |= {"expert_validation": expert, "compatibility_qualified_review": False,
             "model_check_disagreement": disagreement}
    return [{"target_type": target_type, "target_id": tid, "target_content_sha256": digest, "reviewer": reviewer,
             "reviewed_at": reviewed_at, "decision": decision, "reason": reason, "dimensions": dims}
            for tid, digest in targets]
