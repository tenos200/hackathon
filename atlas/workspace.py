"""Pipeline workspace layout. `work/` and `review/` are private (gitignored).

    config/               non-secret pins, budgets, prompts, audits, release
    data/imports/         human-researched source-backed selections
    work/cache/           raw captures + index (private)
    work/stage/           validated stage outputs (JSONL)
    work/model/           recorded model responses + uncertain outcomes (private)
    work/snapshots/       assembled canonical packages
    work/runs/            operational run manifests (timestamps live here only)
    review/               queue, decision template, human decisions, imported reviews (private)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from atlas.models.io import read_jsonl
from atlas.models.records import (
    Assertion, CandidateClaim, CheckerResult, Conflict, CoverageRecord, Entity, Evidence, Explanation, GapRecord,
    Mapping, MechanismContextImport, OpportunityRecord, ResearchContext, Review, SourceDocument, Support,
)
from atlas.review.fingerprint import Catalog


STAGE_PARTS = ("sources", "backbone", "literature")


@dataclass(frozen=True)
class Workspace:
    root: Path

    @property
    def config(self) -> Path: return self.root / "config"
    @property
    def imports(self) -> Path: return self.root / "data" / "imports"
    @property
    def cache(self) -> Path: return self.root / "work" / "cache"
    @property
    def stage(self) -> Path: return self.root / "work" / "stage"
    @property
    def model_dir(self) -> Path: return self.root / "work" / "model"
    @property
    def snapshots(self) -> Path: return self.root / "work" / "snapshots"
    @property
    def runs(self) -> Path: return self.root / "work" / "runs"
    @property
    def review(self) -> Path: return self.root / "review"

    def config_json(self, name: str, default=None):
        path = self.config / name
        if not path.exists():
            if default is not None:
                return default
            raise FileNotFoundError(f"missing config/{name}")
        return json.loads(path.read_text(encoding="utf-8"))

    def stage_file(self, name: str, part: str | None = None) -> Path:
        return (self.stage / part / f"{name}.jsonl") if part else self.stage / f"{name}.jsonl"

    def reviews(self) -> list[Review]:
        return read_jsonl(self.review / "reviews.jsonl", Review)

    def checks(self) -> dict[str, CheckerResult]:
        """Latest checker result per *assertion* ID (mapped through promoted candidates)."""
        results = read_jsonl(self.stage_file("checks", "literature"), CheckerResult)
        promoted_path = self.stage / "literature" / "promoted.json"
        promoted = json.loads(promoted_path.read_text()) if promoted_path.exists() else {}
        out: dict[str, CheckerResult] = {}
        for result in results:
            assertion_id = promoted.get(result.candidate_id)
            if assertion_id:
                out[assertion_id] = result
        return out

    def catalog(self) -> Catalog:
        """Stage outputs plus manual mechanism-context imports, merged into one validated catalog."""
        cat = Catalog()
        for part in STAGE_PARTS:
            for d in read_jsonl(self.stage_file("documents", part), SourceDocument):
                cat.sources[d.id] = d
            for e in read_jsonl(self.stage_file("entities", part), Entity):
                cat.entities[e.id] = e
            for m in read_jsonl(self.stage_file("mappings", part), Mapping):
                cat.mappings[m.id] = m
            for e in read_jsonl(self.stage_file("evidence", part), Evidence):
                cat.evidence[e.id] = e
            for s in read_jsonl(self.stage_file("supports", part), Support):
                cat.supports[s.id] = s
            for a in read_jsonl(self.stage_file("assertions", part), Assertion):
                if a.id in cat.assertions:  # exact duplicates across stages merge their support lists
                    merged = sorted({*cat.assertions[a.id].support_ids, *a.support_ids})
                    a = Assertion.model_validate({**a.payload(), "support_ids": merged})
                cat.assertions[a.id] = a
        for path in (self.stage / "drafts" / "contexts.jsonl", self.imports / "contexts.jsonl"):
            for c in read_jsonl(path, ResearchContext):  # human-written imports override generated drafts
                cat.contexts[c.id] = c
        for line in read_jsonl(self.imports / "mechanism_contexts.jsonl", MechanismContextImport):
            cat.contexts[line.context.id] = line.context
            for e in line.entities:
                cat.entities[e.id] = e
            for e in line.evidence:
                cat.evidence[e.id] = e
            for s in line.supports:
                cat.supports[s.id] = s
            for a in line.assertions:
                if a.origin != "manual_import":
                    raise ValueError(f"{a.id}: mechanism-context imports must keep origin=manual_import")
                cat.assertions[a.id] = a
        for c in read_jsonl(self.imports / "conflicts.jsonl", Conflict):
            cat.conflicts[c.id] = c
        for o in read_jsonl(self.imports / "opportunities.jsonl", OpportunityRecord):
            cat.opportunities[o.id] = o
        for x in read_jsonl(self.imports / "explanations.jsonl", Explanation):
            cat.explanations[x.id] = x
        return cat

    def mechanism_import_reviews(self) -> list[Review]:
        return [r for line in read_jsonl(self.imports / "mechanism_contexts.jsonl", MechanismContextImport)
                for r in line.reviews]

    def coverage(self) -> list[CoverageRecord]:
        records: dict[str, CoverageRecord] = {}
        for part in STAGE_PARTS:
            for c in read_jsonl(self.stage_file("coverage", part), CoverageRecord):
                records[c.id] = c
        return list(records.values())

    def gaps(self) -> list[GapRecord]:
        return read_jsonl(self.imports / "gaps.jsonl", GapRecord)

    def candidates(self) -> list[CandidateClaim]:
        return read_jsonl(self.stage_file("candidates", "literature"), CandidateClaim)

    def write_part(self, part: str, output) -> None:
        """Rewrite one stage part deterministically from an AdapterOutput."""
        from atlas.models.io import write_jsonl

        for kind in ("documents", "entities", "mappings", "evidence", "assertions", "supports", "coverage"):
            write_jsonl(self.stage_file(kind, part), getattr(output, kind))
