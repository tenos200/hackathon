"""`python -m atlas <command>`: the master plan CLI contract.

Every command exits nonzero on validation failure. `--offline`/`--cached`
forbid network access and fail on missing inputs instead of fabricating
records. Paid commands share one durable budget ledger.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _ws(args):
    from atlas.workspace import Workspace

    return Workspace(Path(args.root).resolve())


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True, default=str))


def cmd_sources(args) -> int:
    from atlas.pipeline import run_sources

    ws = _ws(args)
    targets = json.loads(Path(args.config).read_text(encoding="utf-8"))
    out = run_sources(ws, targets, offline=args.offline)
    incomplete = [c for c in out.coverage if c.completion != "complete_for_query"]
    _print({"documents": len(out.documents), "entities": len(out.entities), "mappings": len(out.mappings),
            "incomplete_coverage": [{"source": c.source, "reason": c.failure_reason} for c in incomplete]})
    return 0


def cmd_backbone(args) -> int:
    from atlas.pipeline import run_backbone

    ws = _ws(args)
    targets = json.loads((ws.config / "targets.json").read_text(encoding="utf-8"))
    out = run_backbone(ws, targets)
    _print({"entities": len(out.entities), "assertions": len(out.assertions), "evidence": len(out.evidence)})
    return 0


def _model_setup(ws, args, purpose):
    from atlas.budget import BudgetLedger
    from atlas.extraction.model_client import ResponseCache, make_client
    from atlas.extraction.stage import load_stage_config

    models = ws.config_json("models.json")
    cfg = load_stage_config(models, purpose, ws.config / "prompts")
    cache = ResponseCache(ws.model_dir)
    api_key = None if args.offline else os.environ.get("OPENAI_API_KEY")
    ledger = None
    if api_key:
        if not args.budget_usd or args.budget_usd <= 0:
            raise SystemExit("a paid run needs a positive --budget-usd (approved cap)")
        ledger = BudgetLedger.from_files(ws.root / "work" / "budget_ledger.jsonl", ws.config / "budget.json",
                                         command_cap_usd=args.budget_usd)
    return make_client(offline=args.offline, api_key=api_key, ledger=ledger, cache=cache, models_config=models), cfg


def _alias_index(ws, catalog):
    from atlas.linking.index import AliasIndex
    from atlas.pipeline import load_reference

    index = AliasIndex()
    ontology, _, ids = load_reference(ws)
    index.add_hpo_ontology(ontology, ids[0] if ids else None)
    for entity in catalog.entities.values():
        index.add_entity(entity)
    return index, ontology


def cmd_extract(args) -> int:
    from atlas.budget import BudgetExceeded
    from atlas.extraction.model_client import MissingRecording, PaidCallRefused
    from atlas.extraction.stage import extract_document
    from atlas.models.io import write_jsonl
    from atlas.pipeline import OntologyBackedEntities

    ws = _ws(args)
    catalog = ws.catalog()
    manifest = ws.config / "catalog_manifest.json"
    if not manifest.exists():
        raise SystemExit("freeze the catalog first (python -m atlas catalog-freeze) before bulk extraction")
    client, cfg = _model_setup(ws, args, "extraction")
    index, ontology = _alias_index(ws, catalog)
    entities = OntologyBackedEntities(catalog.entities, ws)
    candidates, runs, failures = [], [], []
    literature = sorted((d for d in catalog.sources.values() if d.source_kind == "literature"), key=lambda d: d.id)
    for document in literature:
        try:
            found, record = extract_document(document, client, cfg, index, entities)
        except (MissingRecording, PaidCallRefused, BudgetExceeded) as exc:
            failures.append({"document": document.external_ref, "reason": str(exc)})
            if isinstance(exc, BudgetExceeded):
                break
            continue
        candidates += found
        runs.append(record)
    write_jsonl(ws.stage_file("candidates", "literature"), candidates)
    (ws.stage / "literature" / "extraction_runs.json").write_text(json.dumps(runs, indent=2, sort_keys=True))
    _print({"documents": len(literature), "candidates": len(candidates),
            "by_state": {s: sum(c.resolution_state == s for c in candidates) for s in ("linked", "ambiguous", "unlinked", "invalid")},
            "failures": failures})
    return 1 if failures and not candidates else 0


def cmd_check(args) -> int:
    from atlas.budget import BudgetExceeded
    from atlas.extraction.model_client import MissingRecording, PaidCallRefused
    from atlas.extraction.stage import check_candidate, load_context_assignments, promote
    from atlas.models.io import write_jsonl
    from atlas.pipeline import phenotype_entities_for
    from atlas.sources.builders import AdapterOutput

    ws = _ws(args)
    catalog = ws.catalog()
    candidates = ws.candidates()
    assignments = load_context_assignments(ws.review / "context_assignments.jsonl")
    out, promoted = promote(candidates, catalog.sources, assignments)
    linked_terms = {a.object_id for a in out.assertions if a.object_id.startswith("HP:")}
    out.entities += [e for e in phenotype_entities_for(ws, linked_terms) if e.id not in catalog.entities]
    client, cfg = _model_setup(ws, args, "checking")
    checks, failures = [], []
    for candidate in candidates:
        if candidate.id not in promoted:
            continue
        try:
            checks.append(check_candidate(candidate, catalog.sources[candidate.source_document_id],
                                          {**catalog.entities, **{e.id: e for e in out.entities}}, client, cfg))
        except (MissingRecording, PaidCallRefused, BudgetExceeded, ValueError) as exc:
            failures.append({"candidate": candidate.id, "reason": str(exc)})
            if isinstance(exc, BudgetExceeded):
                break
    ws.write_part("literature", out)
    write_jsonl(ws.stage_file("checks", "literature"), checks)
    (ws.stage / "literature" / "promoted.json").write_text(json.dumps(promoted, indent=2, sort_keys=True))
    _print({"promoted": len(promoted), "checked": len(checks), "unchecked_remain_pending": len(promoted) - len(checks),
            "failures": failures})
    return 0


def _all_reviews(ws):
    return [*ws.reviews(), *ws.mechanism_import_reviews()]


def cmd_review_export(args) -> int:
    from atlas.checking.structural import structural_report
    from atlas.pipeline import load_reference
    from atlas.review.workflow import export_queue

    ws = _ws(args)
    catalog = ws.catalog()
    ontology, _, _ = load_reference(ws)
    items = export_queue(catalog, _all_reviews(ws), structural_report(catalog, ontology), ws.checks(), ws.review)
    _print({"queue_items": len(items), "queue": str(ws.review / "queue.html"),
            "template": str(ws.review / "decisions.template.jsonl")})
    return 0


def cmd_review_import(args) -> int:
    from atlas.checking.structural import structural_report
    from atlas.models.io import read_jsonl, write_jsonl
    from atlas.models.records import Review
    from atlas.pipeline import load_reference
    from atlas.review.workflow import import_decisions

    ws = _ws(args)
    catalog = ws.catalog()
    ontology, _, _ = load_reference(ws)
    report = import_decisions(Path(args.decisions), catalog, structural_report(catalog, ontology), ws.checks())
    existing = {r.id: r for r in read_jsonl(ws.review / "reviews.jsonl", Review)}
    existing.update({r.id: r for r in report.imported})
    write_jsonl(ws.review / "reviews.jsonl", existing.values())
    _print({"imported": len(report.imported), "rejected": [{"line": n, "reason": r} for n, r in report.rejected]})
    return 1 if report.rejected else 0


def cmd_review_batch(args) -> int:
    """A person's decision applied to a filtered set of records (dry run unless --apply)."""
    from atlas.checking.structural import structural_report
    from atlas.models.io import read_jsonl, write_jsonl
    from atlas.models.records import Review
    from atlas.pipeline import load_reference
    from atlas.review.workflow import batch_decision_rows, batch_targets, import_decisions

    if not args.reviewer.strip() or not args.reason.strip():
        raise SystemExit("--reviewer and --reason must name the person deciding and why")
    ws = _ws(args)
    catalog = ws.catalog()
    ids = set(Path(args.ids_file).read_text().split()) if args.ids_file else None
    targets = batch_targets(catalog, _all_reviews(ws), target_type=args.target_type, predicate=args.predicate,
                            origin=args.origin, source_ref_contains=args.source, context_id=args.context_id, ids=ids)
    sample = [t for t, _ in targets[:10]]
    if not args.apply:
        _print({"dry_run": True, "matching_pending_records": len(targets), "sample_ids": sample,
                "next": "re-run with --apply to record this decision under the named reviewer"})
        return 0
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = batch_decision_rows(targets, target_type=args.target_type, reviewer=args.reviewer, reviewed_at=now,
                               decision=args.decision, reason=args.reason, expert=args.expert,
                               disagreement=args.model_check_disagreement)
    path = ws.review / f"decisions-batch-{now.replace(':', '')}.jsonl"
    ws.review.mkdir(exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    ontology, _, _ = load_reference(ws)
    report = import_decisions(path, catalog, structural_report(catalog, ontology), ws.checks())
    existing = {r.id: r for r in read_jsonl(ws.review / "reviews.jsonl", Review)}
    existing.update({r.id: r for r in report.imported})
    write_jsonl(ws.review / "reviews.jsonl", existing.values())
    _print({"recorded": len(report.imported), "rejected": len(report.rejected),
            "rejections_sample": [r for _, r in report.rejected[:5]], "decisions_file": str(path)})
    return 1 if report.rejected else 0


def cmd_catalog_freeze(args) -> int:
    from atlas.review.workflow import catalog_manifest

    ws = _ws(args)
    manifest = catalog_manifest(ws.catalog())
    (ws.config / "catalog_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    _print({"catalog_sha256": manifest["catalog_sha256"], "entities": len(manifest["entities"])})
    return 0


def cmd_catalog_impact(args) -> int:
    from atlas.review.workflow import catalog_impact

    ws = _ws(args)
    frozen = ws.config_json("catalog_manifest.json")
    impact = catalog_impact(ws.catalog(), frozen, _all_reviews(ws))
    _print(impact)
    return 1 if impact["changed_catalog_ids"] or impact["stale_accepted_reviews"] else 0


def assembly_inputs(ws):
    from atlas.assembly.assemble import AssemblyInputs
    from atlas.pipeline import load_reference

    ontology, reference, ref_ids = load_reference(ws)
    audit_rows = ws.config_json("context_mapping_audit.json", {"contexts": []})["contexts"]
    return AssemblyInputs(
        catalog=ws.catalog(), reviews=_all_reviews(ws), checks=ws.checks(), ontology=ontology, reference=reference,
        reference_source_ids=ref_ids if reference is not None else [],
        audit={row["context_id"]: row for row in audit_rows}, release=ws.config_json("release.json"),
        coverage=ws.coverage(), gaps=ws.gaps())


def cmd_assemble(args) -> int:
    from atlas.assembly.assemble import AssemblyError, assemble, write_package

    if not args.offline:
        raise SystemExit("assemble runs offline only: use --offline")
    ws = _ws(args)
    release = ws.config_json("release.json")
    if not release.get("published_at") or not release.get("title"):
        print("config/release.json needs a human-set published_at and title before assembly", file=sys.stderr)
        return 1
    try:
        package, report = assemble(assembly_inputs(ws))
    except AssemblyError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    path = write_package(package, ws.snapshots)
    ws.runs.mkdir(parents=True, exist_ok=True)
    run = {"snapshot_id": package["snapshot_id"], "assembled_at": datetime.now(timezone.utc).isoformat(),
           "published": report.published, "withheld": report.withheld, "package": path}
    (ws.runs / f"assemble-{package['snapshot_id'][5:17]}.json").write_text(json.dumps(run, indent=2, sort_keys=True))
    _print({"snapshot_id": package["snapshot_id"], "package": path, "published": report.published,
            "withheld_count": len(report.withheld)})
    return 0


def _package_path(ws, snapshot_id: str) -> Path:
    path = ws.snapshots / f"{snapshot_id}.json"
    if not path.exists():
        raise SystemExit(f"no assembled package {path}")
    return path


def cmd_validate(args) -> int:
    from atlas.api.fixture_store import CONTRACTS_DIR
    from atlas.api.real_store import RealStore, validate_store
    from atlas.assembly.package import load_package

    ws = _ws(args)
    package = json.loads(_package_path(ws, args.snapshot).read_text(encoding="utf-8"))
    try:
        content = load_package(package, expected_snapshot_id=args.snapshot)
        checked = validate_store(RealStore(args.snapshot, content), CONTRACTS_DIR / "openapi.json")
    except Exception as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1
    _print({"snapshot_id": args.snapshot, "valid": True, "route_payloads_validated": checked})
    return 0


def cmd_publish(args) -> int:
    from atlas.storage.postgres import publish

    dsn = os.environ.get("ATLAS_PUBLISH_DATABASE_URL")
    if not dsn:
        raise SystemExit("ATLAS_PUBLISH_DATABASE_URL (publisher role) is not set")
    ws = _ws(args)
    package = json.loads(_package_path(ws, args.snapshot).read_text(encoding="utf-8"))
    result = publish(dsn, package)
    _print(asdict(result))
    return 0


def cmd_serve(args) -> int:
    import uvicorn

    ws = _ws(args)
    env = {"ATLAS_DATA_MODE": "real", "ATLAS_SNAPSHOT_ID": args.snapshot}
    if not os.environ.get("ATLAS_DATABASE_URL"):
        env |= {"ATLAS_SNAPSHOT_BACKEND": "file", "ATLAS_SNAPSHOT_FILE": str(_package_path(ws, args.snapshot))}
    os.environ.update(env)
    uvicorn.run("atlas.api.main:app", host=args.host, port=args.port)
    return 0


def cmd_migrate(args) -> int:
    from atlas.storage.postgres import apply_migrations

    dsn = os.environ.get(args.dsn_env)
    if not dsn:
        raise SystemExit(f"{args.dsn_env} (migration/admin identity) is not set")
    _print({"applied": apply_migrations(dsn)})
    return 0


def cmd_budget_status(args) -> int:
    from atlas.budget import BudgetLedger

    ws = _ws(args)
    ledger = BudgetLedger.from_files(ws.root / "work" / "budget_ledger.jsonl", ws.config / "budget.json")
    _print({"config": ledger.config, "spent": ledger.summary()})
    return 0


def cmd_real_ingest(args) -> int:
    from atlas.real_data import run_real_ingest

    ws = _ws(args)
    summary = run_real_ingest(ws, Path(args.data_dir).resolve())
    _print(summary)
    return 0


def cmd_fixture_check(args) -> int:
    from atlas.api.fixture_store import FixtureStore

    store = FixtureStore()
    _print({"contract": "1.0.0", "fixture_snapshot": store.snapshot_id, "checksums": "verified"})
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m atlas")
    parser.add_argument("--root", default=str(ROOT), help="workspace root (default: repository root)")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("sources"); p.add_argument("--config", default=str(ROOT / "config" / "targets.json"))
    p.add_argument("--offline", action="store_true"); p.set_defaults(fn=cmd_sources)
    p = sub.add_parser("backbone"); p.add_argument("--cached", action="store_true", required=True); p.set_defaults(fn=cmd_backbone)
    for name, fn in (("extract", cmd_extract), ("check", cmd_check)):
        p = sub.add_parser(name)
        p.add_argument("--cached-sources", action="store_true", required=True)
        p.add_argument("--budget-usd", type=float, default=0.0)
        p.add_argument("--offline", action="store_true", help="recorded responses only; no model calls")
        p.set_defaults(fn=fn)
    sub.add_parser("review-export").set_defaults(fn=cmd_review_export)
    p = sub.add_parser("review-import"); p.add_argument("decisions"); p.set_defaults(fn=cmd_review_import)
    p = sub.add_parser("review-batch", help="a named person's decision on a filtered set (dry run unless --apply)")
    p.add_argument("--reviewer", required=True); p.add_argument("--reason", required=True)
    p.add_argument("--decision", choices=["accepted", "rejected", "needs_context"], default="accepted")
    p.add_argument("--target-type", default="assertion",
                   choices=["assertion", "context", "mapping", "conflict", "opportunity", "explanation"])
    p.add_argument("--predicate"); p.add_argument("--origin"); p.add_argument("--source", help="source external_ref substring")
    p.add_argument("--context-id"); p.add_argument("--ids-file")
    p.add_argument("--expert", action="store_true", help="only for an actual qualified expert review")
    p.add_argument("--model-check-disagreement"); p.add_argument("--apply", action="store_true")
    p.set_defaults(fn=cmd_review_batch)
    sub.add_parser("catalog-freeze").set_defaults(fn=cmd_catalog_freeze)
    sub.add_parser("catalog-impact").set_defaults(fn=cmd_catalog_impact)
    p = sub.add_parser("assemble"); p.add_argument("--offline", action="store_true"); p.set_defaults(fn=cmd_assemble)
    for name, fn in (("validate", cmd_validate), ("publish", cmd_publish)):
        p = sub.add_parser(name); p.add_argument("--snapshot", required=True); p.set_defaults(fn=fn)
    p = sub.add_parser("serve"); p.add_argument("--snapshot", required=True)
    p.add_argument("--host", default="127.0.0.1"); p.add_argument("--port", type=int, default=8000); p.set_defaults(fn=cmd_serve)
    p = sub.add_parser("migrate"); p.add_argument("--dsn-env", default="ATLAS_ADMIN_DATABASE_URL"); p.set_defaults(fn=cmd_migrate)
    sub.add_parser("budget-status").set_defaults(fn=cmd_budget_status)
    sub.add_parser("fixture-check").set_defaults(fn=cmd_fixture_check)
    p = sub.add_parser("real-ingest", help="register the uploaded captures and run all source loaders offline")
    p.add_argument("--data-dir", default=str(ROOT / "hackathon-claude-repo" / "data")); p.set_defaults(fn=cmd_real_ingest)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.fn(args) or 0)
