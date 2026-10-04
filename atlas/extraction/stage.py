"""Stage 3 (extraction), stage 4 (linking/structural validation) and promotion.

One model call per source document, cached by canonical text hash, prompt and
schema version, model and settings. The model's statement status, direction
and scope are kept exactly as returned; code never reclassifies meaning with
word lists. Code only places verbatim quotes, links exact identifiers and
checks types. Anything it cannot place or link becomes `ambiguous`, `unlinked`
or `invalid` with a reason, never a broader claim. New entities suggested by
the model are not minted; they go to manual identity review.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from atlas.extraction.model_client import ModelClient, ModelRequest
from atlas.extraction.schemas import CHECK_DIMENSIONS, CHECK_SCHEMA, CHECK_SCHEMA_VERSION, EXTRACTION_SCHEMA, EXTRACTION_SCHEMA_VERSION
from atlas.hashing import canonical_json, content_sha256, stable_id
from atlas.linking.index import AliasIndex, LookupResult
from atlas.models.predicates import check_predicate
from atlas.models.records import (
    CandidateClaim, CheckDimension, CheckerDimensions, CheckerResult, Entity, Scope, SourceDocument, Span,
)
from atlas.sources.builders import AdapterOutput, claim, text_evidence_from_spans
from atlas.sources.canonical import SpanError, locate_quote


@dataclass(frozen=True)
class StageModelConfig:
    model: str
    prompt_version: str
    instructions: str
    settings: dict[str, Any]
    max_output_tokens: int


def load_stage_config(models_config: dict[str, Any], purpose: str, prompts_dir: Path) -> StageModelConfig:
    cfg = models_config.get(purpose, {})
    if not cfg.get("model"):
        raise ValueError(f"config/models.json: {purpose}.model is not set; choose and verify a model before a paid run")
    prompt_version = cfg["prompt_version"]
    instructions = (prompts_dir / f"{prompt_version}.txt").read_text(encoding="utf-8")
    return StageModelConfig(model=cfg["model"], prompt_version=prompt_version, instructions=instructions,
                            settings=cfg.get("settings", {}), max_output_tokens=int(cfg.get("max_output_tokens", 4000)))


def _source_input(document: SourceDocument) -> str:
    sections = "\n".join(f"- {s.label}: [{s.start},{s.end})" for s in document.sections)
    return f"Sections:\n{sections}\n<source>\n{document.canonical_text}\n</source>"


def _link(index: AliasIndex, mention: str, entity_type: str) -> tuple[str | None, str | None, LookupResult]:
    result = index.lookup(mention, entity_type)
    if result.resolved_id:
        return result.resolved_id, None, result
    if result.ambiguous:
        return None, f"{entity_type} mention {mention!r} is ambiguous: {', '.join(result.candidates)}", result
    if result.candidates:
        return None, (f"{entity_type} mention {mention!r} matches only non-identity aliases "
                      f"({', '.join(result.candidates)}); needs contextual identity review"), result
    retrieved = index.retrieve(mention, entity_type)
    hint = f"; lexical candidates: {', '.join(retrieved)}" if retrieved else ""
    return None, f"{entity_type} mention {mention!r} is not in the frozen catalog{hint}", result


def extract_document(document: SourceDocument, client: ModelClient, cfg: StageModelConfig, index: AliasIndex,
                     entities: dict[str, Entity]) -> tuple[list[CandidateClaim], dict[str, Any]]:
    request = ModelRequest(
        purpose="extraction", model=cfg.model, prompt_version=f"{cfg.prompt_version}/{EXTRACTION_SCHEMA_VERSION}",
        schema_name="atlas_extraction", schema=EXTRACTION_SCHEMA, instructions=cfg.instructions,
        input_text=_source_input(document), settings=cfg.settings, max_output_tokens=cfg.max_output_tokens,
        content_hash=document.canonical_sha256)
    response = client.call(request)
    output = response.output
    candidates: list[CandidateClaim] = []
    for position, raw in enumerate(output.get("claims", [])):
        reasons: list[str] = []
        state = "linked"
        spans: list[Span] = []
        for quote in raw["support_quotes"]:
            try:
                spans.append(locate_quote(document.canonical_text, quote["quote"], document.sections,
                                          quote["section_label"]))
            except SpanError as exc:
                reasons.append(f"quote not placeable: {exc}")
                state = "invalid"
        if not raw["support_quotes"]:
            reasons.append("no supporting quote")
            state = "invalid"
        subject_id, subject_reason, subject_result = _link(index, raw["subject_mention"], raw["subject_type"])
        object_id, object_reason, object_result = _link(index, raw["object_mention"], raw["object_type"])
        variant_ids: list[str] = []
        for mention in raw["scope"]["variant_mentions"]:
            vid, vreason, _ = _link(index, mention, "variant")
            if vid:
                variant_ids.append(vid)
            else:
                reasons.append(vreason or f"variant {mention!r} unresolved")
                state = "unlinked" if state == "linked" else state
        for reason, result in ((subject_reason, subject_result), (object_reason, object_result)):
            if reason:
                reasons.append(reason)
                if state == "linked":
                    state = "ambiguous" if result.ambiguous else "unlinked"
        if state == "linked":
            problems = check_predicate(raw["predicate"], entities.get(subject_id), entities.get(object_id),
                                       raw["effect_direction"], raw["scope"]["outcome_text"])
            if problems:
                reasons += problems
                state = "invalid"
        scope_raw = {k: v for k, v in raw["scope"].items() if k != "variant_mentions"}
        scope = Scope(variant_ids=sorted(variant_ids), **scope_raw)
        body = {"document": document.id, "response": response.response_id, "position": position, "claim": raw}
        candidates.append(CandidateClaim(
            id=stable_id("cand", body), source_document_id=document.id, subject_mention=raw["subject_mention"],
            subject_type=raw["subject_type"], object_mention=raw["object_mention"], object_type=raw["object_type"],
            predicate=raw["predicate"], scope=scope, effect_direction=raw["effect_direction"],
            statement_status=raw["statement_status"], spans=sorted(spans, key=lambda s: (s.start, s.end)),
            linked_subject_id=subject_id, linked_object_id=object_id, context_id=None, resolution_state=state,
            reason="; ".join(reasons) or None, model_response_id=response.response_id))
    record = {"document_id": document.id, "response_id": response.response_id, "abstained": output.get("abstained"),
              "abstention_reason": output.get("abstention_reason"), "candidate_count": len(candidates),
              "recorded": response.recorded, "patient_counts": [c.get("patient_count") for c in output.get("claims", [])]}
    return candidates, record


@dataclass(frozen=True)
class ContextAssignment:
    """A human's assignment of a linked candidate to a research context and study metadata."""

    candidate_id: str
    context_id: str | None
    study_design: str
    study_or_cohort_ids: list[str]
    independence: str
    patient_count: int | None
    assigned_by: str


def load_context_assignments(path: Path) -> dict[str, ContextAssignment]:
    """Read the person-written review/context_assignments.jsonl (strict keys)."""
    assignments: dict[str, ContextAssignment] = {}
    if path.exists():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if line.strip():
                row = json.loads(line)
                expected = set(ContextAssignment.__dataclass_fields__)
                if set(row) != expected:
                    raise ValueError(f"{path}:{number}: expected keys {sorted(expected)}")
                assignments[row["candidate_id"]] = ContextAssignment(**row)
    return assignments


def write_context_assignments(path: Path, assignments: list[ContextAssignment]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(asdict(a), sort_keys=True) + "\n"
                            for a in sorted(assignments, key=lambda a: a.candidate_id)), encoding="utf-8")


def promote(candidates: list[CandidateClaim], documents: dict[str, SourceDocument],
            assignments: dict[str, ContextAssignment]) -> tuple[AdapterOutput, dict[str, str]]:
    """Linked candidates become pending Assertion/Evidence/Support records (still unreviewed)."""
    out = AdapterOutput()
    promoted: dict[str, str] = {}
    for candidate in candidates:
        if candidate.resolution_state != "linked":
            continue
        assignment = assignments.get(candidate.id)
        document = documents[candidate.source_document_id]
        evidence = text_evidence_from_spans(
            document, candidate.spans,
            study_design=assignment.study_design if assignment else "not recorded",
            species=candidate.scope.species,
            patient_count=assignment.patient_count if assignment else None,
            study_or_cohort_ids=assignment.study_or_cohort_ids if assignment else [],
            independence=assignment.independence if assignment else "unknown")
        assertion = claim(out, subject_id=candidate.linked_subject_id, predicate=candidate.predicate,
                          object_id=candidate.linked_object_id,
                          context_id=assignment.context_id if assignment else None, scope=candidate.scope,
                          effect_direction=candidate.effect_direction, statement_status=candidate.statement_status,
                          origin="literature_extraction", evidence=evidence)
        promoted[candidate.id] = assertion.id
    return out, promoted


def check_candidate(candidate: CandidateClaim, document: SourceDocument, entities: dict[str, Entity],
                    client: ModelClient, cfg: StageModelConfig) -> CheckerResult:
    """Stage 5: contextual check with the full source text, spans, assertion and entity definitions."""
    definitions = {eid: {"type": entities[eid].type, "label": entities[eid].label,
                         "aliases": [a.text for a in entities[eid].aliases][:20]}
                   for eid in (candidate.linked_subject_id, candidate.linked_object_id) if eid in entities}
    proposal = {"subject": candidate.linked_subject_id, "predicate": candidate.predicate,
                "object": candidate.linked_object_id, "statement_status": candidate.statement_status,
                "effect_direction": candidate.effect_direction, "scope": candidate.scope.payload(),
                "supporting_spans": [s.payload() for s in candidate.spans]}
    input_text = (f"Proposed assertion:\n{canonical_json(proposal)}\nLinked entity definitions:\n"
                  f"{canonical_json(definitions)}\n" + _source_input(document))
    candidate_hash = content_sha256(candidate.payload())
    request = ModelRequest(
        purpose="checking", model=cfg.model, prompt_version=f"{cfg.prompt_version}/{CHECK_SCHEMA_VERSION}",
        schema_name="atlas_check", schema=CHECK_SCHEMA, instructions=cfg.instructions, input_text=input_text,
        settings=cfg.settings, max_output_tokens=cfg.max_output_tokens, content_hash=candidate_hash)
    response = client.call(request)
    dims: dict[str, CheckDimension] = {}
    for name in CHECK_DIMENSIONS:
        raw = response.output.get(name)
        if raw is None:
            raise ValueError(f"checker response lacks dimension {name}; result stays pending")
        spans, unplaced = [], []
        for quote in raw["quotes"]:
            try:
                spans.append(locate_quote(document.canonical_text, quote, document.sections, None))
            except SpanError:
                unplaced.append(quote)
        reason = raw["reason"] + (f" [unplaceable quotes omitted: {len(unplaced)}]" if unplaced else "")
        dims[name] = CheckDimension(verdict=raw["verdict"], reason=reason or "no reason given", evidence_spans=spans)
    return CheckerResult(id=stable_id("check", [candidate.id, candidate_hash, response.response_id]),
                         candidate_id=candidate.id, candidate_content_sha256=candidate_hash,
                         model_response_id=response.response_id, dimensions=CheckerDimensions(**dims))


def current_checks(checks: list[CheckerResult], candidates: dict[str, CandidateClaim]) -> list[CheckerResult]:
    """Drop checks whose candidate changed since checking (stale checks are invalid)."""
    return [c for c in checks if c.candidate_id in candidates
            and content_sha256(candidates[c.candidate_id].payload()) == c.candidate_content_sha256]
