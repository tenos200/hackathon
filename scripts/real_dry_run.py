"""DRY RUN of the full backend on the real uploaded data, in a scratch workspace.

Acceptance is SIMULATED by a test stand-in so assembly, analytics and every API
route can be exercised at real scale. The package is written only to the
scratch directory and discarded; it must never be published or served as a
reviewed snapshot. Only counts, timings and validation results are kept in
reports/real_dry_run.json.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

STAND_IN = "DRY-RUN STAND-IN (simulated acceptance, not a human decision)"


def main() -> int:
    from atlas.api.fixture_store import CONTRACTS_DIR
    from atlas.api.real_store import RealStore, validate_store
    from atlas.assembly.assemble import assemble
    from atlas.assembly.package import load_package
    from atlas.checking.structural import structural_report
    from atlas.cli import assembly_inputs
    from atlas.models.io import write_jsonl
    from atlas.pipeline import load_reference
    from atlas.review.workflow import batch_decision_rows, batch_targets, import_decisions
    from atlas.workspace import Workspace

    if not (ROOT / "work" / "stage" / "inventory.json").exists():
        print("run `python -m atlas real-ingest` first", file=sys.stderr)
        return 1
    report: dict = {"WARNING": "Simulated acceptance; this package was discarded and is not a reviewed snapshot."}
    with tempfile.TemporaryDirectory(prefix="atlas-dry-run-") as tmp:
        root = Path(tmp)
        shutil.copytree(ROOT / "config", root / "config")
        shutil.copytree(ROOT / "data", root / "data", symlinks=True)
        shutil.copytree(ROOT / "work" / "stage", root / "work" / "stage")
        (root / "work" / "cache").symlink_to(ROOT / "work" / "cache")
        release = {"published_at": "2026-10-03T00:00:00Z", "title": "DRY RUN (simulated acceptance)",
                   "limitations": ["DRY RUN: not a reviewed snapshot."], "example_context_ids": []}
        (root / "config" / "release.json").write_text(json.dumps(release))
        ws = Workspace(root)
        started = time.perf_counter()
        catalog = ws.catalog()
        ontology, _, _ = load_reference(ws)
        structural = structural_report(catalog, ontology)
        report["catalog"] = {"assertions": len(catalog.assertions), "contexts": len(catalog.contexts),
                             "entities": len(catalog.entities), "structural_failures": len(structural)}
        recorded = 0
        for target_type in ("mapping", "assertion", "context"):
            targets = batch_targets(catalog, [], target_type=target_type)
            rows = batch_decision_rows(targets, target_type=target_type, reviewer=STAND_IN,
                                       reviewed_at="2026-10-03T00:00:00Z", decision="accepted",
                                       reason="dry run only", expert=False, disagreement=None)
            path = root / "review" / f"{target_type}.jsonl"
            path.parent.mkdir(exist_ok=True)
            path.write_text("".join(json.dumps(r) + "\n" for r in rows))
            result = import_decisions(path, catalog, structural, {})
            recorded += len(result.imported)
            existing = ws.reviews()
            write_jsonl(ws.review / "reviews.jsonl", [*existing, *result.imported])
        report["simulated_acceptances"] = recorded
        package, run = assemble(assembly_inputs(ws))
        report["assembly_seconds"] = round(time.perf_counter() - started, 1)
        report["published_counts"] = run.published
        report["withheld"] = len(run.withheld)
        report["withheld_reasons_sample"] = sorted({r for reasons in run.withheld.values() for r in reasons})[:8]
        store = RealStore(package["snapshot_id"], load_package(package))
        t = time.perf_counter()
        report["route_payloads_validated"] = validate_store(store, CONTRACTS_DIR / "openapi.json")
        report["validation_seconds"] = round(time.perf_counter() - t, 1)
        ext = store.ext
        clusters = ext.clusters().data
        report["clusters"] = [{"basis": c.basis, "label": c.label, "members": len(c.members)} for c in clusters.clusters]
        report["unclustered_contexts"] = len(clusters.unclustered)
        contexts = sorted(store.contexts)
        ranked = sum(1 for c in store.c.comparisons if c.ranking_basis != "unranked")
        report["comparisons"] = {"cards": len(store.c.comparisons), "ranked": ranked}
        overlaps = {cid: len(ext.network(cid).data.overlaps) for cid in contexts}
        report["contexts_with_network_overlap"] = sum(1 for v in overlaps.values() if v)
        graph = ext.graph("ctx:MONDO:0100135", 2).data if "ctx:MONDO:0100135" in store.contexts else None
        if graph:
            report["dravet_graph_depth2"] = {"nodes": len(graph.nodes), "links": len(graph.links), "truncated": graph.truncated}
        dravet = store.connections("ctx:MONDO:0100135").data if "ctx:MONDO:0100135" in store.contexts else None
        if dravet:
            report["dravet_connections"] = [
                {"other": c.context_b.label, "basis": c.ranking_basis, "baseline": c.disease_baseline.score,
                 "subgroup": c.subgroup_comparison.score, "specific_terms": c.disease_baseline.specific_shared_term_ids[:3]}
                for c in dravet.comparisons]
        sample = next((c for c in contexts if overlaps.get(c)), None)
        if sample:
            net = ext.network(sample).data
            report["network_overlap_example"] = {"context": store.contexts[sample].record.label, "overlaps": [
                {"other": o.other_context.label, "shared": [f"{s.entity.label} ({s.entity_type}, {s.role})" for s in o.shared[:3]]}
                for o in net.overlaps[:3]]}
        report["search_SCN2A"] = [m.label for m in store.search("SCN2A").data.matches][:5]
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports" / "real_dry_run.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
