"""Deterministic structural checks (stage 4). A failure cannot be overridden by review.

These verify types, identifiers, exact spans and that recorded qualifiers are
present verbatim in the cited source context. They make no semantic judgment:
whether text supports a claim, its direction, status or scope is decided by a
human reviewer, aided by the contextual model check. A failed qualifier blocks
and requeues the assertion (`needs_context`); it is never silently removed.
"""

from __future__ import annotations

import re

from atlas.models.predicates import check_predicate
from atlas.models.records import Assertion, Evidence, SourceDocument
from atlas.review.fingerprint import Catalog
from atlas.sources.canonical import SpanError, verify_span
from atlas.sources.hpo import HpoOntology

# Statement statuses a human-labelled protocol/registration document may carry.
PROTOCOL_STATUSES = frozenset({"planned", "proposed", "ongoing", "background", "listed_record"})


def evidence_window(source: SourceDocument, evidence: Evidence) -> str:
    """The permitted context a reviewer sees: sections containing the spans, or the whole text."""
    if evidence.kind != "text_spans":
        return ""
    sections = [source.section_for(s.start, s.end) for s in evidence.spans]
    if sections and all(sections):
        start = min(s.start for s in sections)
        end = max(s.end for s in sections)
        return source.canonical_text[start:end]
    return source.canonical_text


def check_evidence(evidence: Evidence, source: SourceDocument | None) -> list[str]:
    if source is None:
        return [f"evidence {evidence.id} cites a source version that is not captured"]
    problems: list[str] = []
    for span in evidence.spans:
        try:
            verify_span(source.canonical_text, span)
        except SpanError as exc:
            problems.append(f"evidence {evidence.id}: {exc}")
    if evidence.kind == "structured_record":
        match = re.fullmatch(r"(?:phenotype\.hpoa|gaf):line:(\d+)", evidence.record_locator or "")
        if match:
            lines = source.canonical_text.split("\n")
            number = int(match.group(1))
            line = lines[number - 1] if 0 < number <= len(lines) else ""
            cells = line.split("\t")
            for field in evidence.record_payload or []:
                if field.value and field.value not in cells:
                    problems.append(f"evidence {evidence.id}: field {field.name}={field.value!r} is not on {evidence.record_locator}")
    if evidence.patient_count is not None and evidence.kind == "text_spans":
        window = evidence_window(source, evidence)
        if not re.search(rf"(?<!\d){evidence.patient_count}(?!\d)", window):
            problems.append(f"evidence {evidence.id}: patient count {evidence.patient_count} does not appear in the cited context")
    return problems


def _qualifier_supported(qualifier: str, evidence: Evidence, source: SourceDocument) -> bool:
    if evidence.kind == "text_spans":
        return qualifier in evidence_window(source, evidence)
    values = {f.value for f in evidence.record_payload or []} | {f"{f.name}: {f.value}" for f in evidence.record_payload or []}
    if qualifier in values:
        return True
    _, sep, value = qualifier.partition(": ")
    return bool(sep) and value in values


def check_assertion(assertion: Assertion, catalog: Catalog, ontology: HpoOntology | None = None) -> list[str]:
    problems: list[str] = []
    subject = catalog.entities.get(assertion.subject_id)
    obj = catalog.entities.get(assertion.object_id)
    problems += check_predicate(assertion.predicate, subject, obj, assertion.effect_direction, assertion.scope.outcome_text)
    if assertion.context_id is not None:
        context = catalog.contexts.get(assertion.context_id)
        if context is None:
            problems.append(f"context {assertion.context_id} does not exist")
        elif assertion.predicate == "has_phenotype" and context.disease_id != assertion.subject_id:
            problems.append("a scoped has_phenotype subject must be the context's disease")
    for variant_id in assertion.scope.variant_ids:
        entity = catalog.entities.get(variant_id)
        if entity is None or entity.type != "variant":
            problems.append(f"scope variant {variant_id} does not resolve to a variant entity")
    if assertion.predicate == "has_phenotype" and ontology is not None and obj is not None:
        if ontology.resolve(obj.id) != obj.id:
            problems.append(f"{obj.id} is not a current term in the pinned HPO release")
        elif not ontology.is_phenotypic(obj.id):
            problems.append(f"{obj.id} is not a phenotypic-abnormality term")
    if not assertion.support_ids:
        problems.append("assertion has no support records")
    supporting: list[tuple[Evidence, SourceDocument]] = []
    for support_id in assertion.support_ids:
        support = catalog.supports.get(support_id)
        if support is None or support.assertion_id != assertion.id:
            problems.append(f"support {support_id} is missing or belongs to another assertion")
            continue
        evidence = catalog.evidence.get(support.evidence_id)
        if evidence is None:
            problems.append(f"support {support_id} cites missing evidence {support.evidence_id}")
            continue
        source = catalog.sources.get(evidence.source_document_id)
        problems += check_evidence(evidence, source)
        if source is None:
            continue
        if support.stance == "supports":
            supporting.append((evidence, source))
        if (source.source_metadata.get("document_role") == "protocol"
                and assertion.statement_status not in PROTOCOL_STATUSES and support.stance == "supports"):
            problems.append(f"{source.external_ref} is a protocol; it cannot support a {assertion.statement_status} claim")
    if not supporting:
        problems.append("assertion has no resolvable supporting evidence")
    for field, qualifier in assertion.scope.text_qualifiers():
        if supporting and not any(_qualifier_supported(qualifier, e, s) for e, s in supporting):
            problems.append(f"scope {field} {qualifier!r} is not present verbatim in any supporting source context")
    return sorted(set(problems))


def structural_report(catalog: Catalog, ontology: HpoOntology | None = None) -> dict[str, list[str]]:
    return {aid: p for aid in sorted(catalog.assertions)
            if (p := check_assertion(catalog.assertions[aid], catalog, ontology))}
