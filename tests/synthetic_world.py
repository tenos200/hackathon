"""Builds a complete offline pipeline workspace from synthetic source fixtures.

Everything here is invented test data run through the real pipeline code:
recorded captures replace network access, a scripted client stands in for
recorded model responses, and `TEST_REVIEWER` stands in for a human reviewer.
The product code never records acceptance itself; tests do so explicitly and
label it as a synthetic decision.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from atlas.extraction.model_client import ModelRequest, ModelResponse, ResponseCache
from atlas.extraction.stage import (
    ContextAssignment, StageModelConfig, check_candidate, extract_document, load_context_assignments, promote,
    write_context_assignments,
)
from atlas.hashing import canonical_json, content_sha256
from atlas.linking.index import AliasIndex
from atlas.models.io import write_jsonl
from atlas.models.records import (
    Entity, Explanation, GapRecord, MechanismContextImport, OpportunityRecord, ResearchContext, Scope, SentenceRecord,
)
from atlas.pipeline import EUTILS_EFETCH, HGNC_FETCH, MEDRXIV_DETAILS, CTGOV_STUDY, OntologyBackedEntities, load_reference, run_backbone, run_sources
from atlas.review.workflow import export_queue, fingerprint, import_decisions
from atlas.checking.structural import structural_report
from atlas.sources.builders import make_assertion, make_support, text_evidence
from atlas.sources.fetch import Fetcher, target_url
from atlas.workspace import Workspace

SYN = Path(__file__).parent / "fixtures" / "synthetic_sources"
BASE = "https://synthetic.invalid"
TEST_REVIEWER = "TEST FIXTURE reviewer (synthetic stand-in, not a human decision)"
REVIEWED_AT = "2026-10-03T12:00:00Z"
FETCHED_AT = "2026-10-03T10:00:00.000000Z"
DISEASES = ["MONDO:9900001", "MONDO:9900002", "MONDO:9900003", "MONDO:9900004", "MONDO:9900005",
            "MONDO:9900061", "MONDO:9900062"]
PUBMED_PARAMS = {"db": "pubmed", "id": "99000001,99000002", "retmode": "xml", "tool": "atlas-pipeline"}


def targets() -> dict[str, Any]:
    return {
        "hpo": {"obo_url": f"{BASE}/hp.obo", "hpoa_url": f"{BASE}/phenotype.hpoa", "release": "synthetic-2026-10-01"},
        "mondo": {"obo_url": f"{BASE}/mondo.obo", "sssom_url": f"{BASE}/mondo.sssom.tsv", "release": "synthetic-2026-10-01"},
        "genes": ["SYNTHA", "SYNTHB"],
        "diseases": DISEASES,
        "pubmed": {"pmids": {"99000001": {"public_text_policy": "full"}, "99000002": {"public_text_policy": "excerpt"}}},
        "medrxiv": [{"doi": "10.0000/synthetic.preprint1", "version": "1", "public_text_policy": "link_only",
                     "publication_family_id": None, "document_role": "protocol"}],
        "clinicaltrials": [{"nct": "NCT99999901", "condition_disease_ids": {"Synthetic Disorder A": "MONDO:9900001"}}],
    }


APPROVED = {"pages": [
    {"url": f"{BASE}/alpha", "fetch_backend": "direct", "transformation": None,
     "expect_text": "Synthetic Alpha Foundation", "purpose": "community page", "title": "Synthetic Alpha Foundation",
     "source_kind": "organization_website", "public_text_policy": "full"},
    {"url": f"{BASE}/beta", "fetch_backend": "brightdata", "transformation": None,
     "expect_text": "Synthetic Beta Network", "purpose": "community page", "title": "Synthetic Beta Network",
     "source_kind": "organization_website", "public_text_policy": "full"},
    {"url": f"{BASE}/gamma-lab", "fetch_backend": "direct", "transformation": None, "expect_text": None,
     "purpose": "lab page", "title": "Synthetic Gamma Lab", "source_kind": "institutional_website",
     "public_text_policy": "full"},
    {"url": f"{BASE}/gamma-community", "fetch_backend": "direct", "transformation": None, "expect_text": None,
     "purpose": "community page (not captured)", "title": "Synthetic Gamma Community",
     "source_kind": "organization_website", "public_text_policy": "full"},
]}

RECORDED = [  # (target URL, backend, fixture file)
    (f"{BASE}/hp.obo", "direct", "hp_synthetic.obo"),
    (f"{BASE}/phenotype.hpoa", "direct", "phenotype_synthetic.hpoa"),
    (f"{BASE}/mondo.obo", "direct", "mondo_synthetic.obo"),
    (f"{BASE}/mondo.sssom.tsv", "direct", "mondo_synthetic.sssom.tsv"),
    (HGNC_FETCH + "SYNTHA", "direct", "hgnc_SYNTHA.json"),
    (HGNC_FETCH + "SYNTHB", "direct", "hgnc_SYNTHB.json"),
    (target_url(EUTILS_EFETCH, PUBMED_PARAMS), "direct", "pubmed_synthetic.xml"),
    (f"{MEDRXIV_DETAILS}10.0000/synthetic.preprint1/na/json", "direct", "medrxiv_synthetic.json"),
    (CTGOV_STUDY + "NCT99999901", "direct", "ctgov_NCT99999901.json"),
    (f"{BASE}/alpha", "direct", "org_alpha.html"),
    (f"{BASE}/beta", "brightdata", "org_beta.html"),
    (f"{BASE}/gamma-lab", "direct", "lab_gamma.html"),
]

MODELS = {
    "extraction": {"model": "recorded-synthetic-model", "prompt_version": "extraction_v1", "settings": {},
                   "max_output_tokens": 2000},
    "checking": {"model": "recorded-synthetic-model", "prompt_version": "checking_v1", "settings": {},
                 "max_output_tokens": 1000},
    "pricing": {},
}


class ScriptedClient:
    """Stand-in for recorded model output: returns scripted JSON and records it like a real response."""

    def __init__(self, cache: ResponseCache, script: Callable[[ModelRequest], dict]) -> None:
        self.cache = cache
        self.script = script
        self.calls = 0

    def call(self, request: ModelRequest) -> ModelResponse:
        hit = self.cache.get(request.cache_key())
        if hit is not None:
            return hit
        self.calls += 1
        output = self.script(request)
        response = ModelResponse(response_id=f"resp_synthetic_{request.cache_key()[:16]}", model=request.model,
                                 output=output, input_tokens=None, output_tokens=None, recorded=False)
        self.cache.put(request.cache_key(), response, None)
        return response


def _scope(**values) -> dict:
    base = {k: None for k in ("population_text", "age_text", "species", "cell_or_tissue", "assay_text",
                              "outcome_text", "comparator_text", "timeframe_text")}
    return {**base, "other_qualifiers": [], "variant_mentions": [], **values}


def _claim(subject, stype, predicate, obj, otype, status, quotes, direction="not_applicable", **scope):
    return {"subject_mention": subject, "subject_type": stype, "predicate": predicate, "object_mention": obj,
            "object_type": otype, "statement_status": status, "effect_direction": direction, "patient_count": None,
            "scope": _scope(**scope), "support_quotes": [{"quote": q, "section_label": label} for q, label in quotes]}


POP_A = "12 individuals with synthetic disorder A and SYNTHA loss-of-function variants"
POP_B = "9 individuals with synthetic disorder B and SYNTHB loss-of-function variants"
EXTRACTIONS = {
    "PMID:99000001": {"abstained": False, "abstention_reason": None, "claims": [
        _claim("synthetic disorder A", "disease", "has_phenotype", "synthetic seizure", "phenotype", "reported_result",
               [(POP_A, "RESULTS"), ("synthetic seizure was observed in all individuals", "RESULTS")], population_text=POP_A),
        _claim("synthetic disorder A", "disease", "has_phenotype", "synthetic hypotonia", "phenotype", "reported_result",
               [(POP_A, "RESULTS"), ("synthetic hypotonia in 7 individuals", "RESULTS")], population_text=POP_A),
        _claim("synthetic disorder A", "disease", "has_phenotype", "Synthetic ataxia", "phenotype", "negated",
               [("Synthetic ataxia was not observed in this cohort.", "RESULTS")]),
        _claim("synthetic disorder A", "disease", "has_phenotype", "synthetic developmental delay", "phenotype", "planned",
               [("We plan to assess synthetic developmental delay in a follow-up study.", "CONCLUSIONS")]),
        _claim("SYNTHA", "gene", "gene_associated_with_disease", "synthetic disorder A", "disease", "background",
               [("SYNTHA has been associated with synthetic disorder A in earlier reports.", "BACKGROUND")]),
        _claim("SYNA1", "gene", "gene_associated_with_disease", "synthetic disorder A", "disease", "background",
               [("SYNTHA has been associated with synthetic disorder A in earlier reports.", "BACKGROUND")]),
        _claim("synthetic disorder A", "disease", "has_phenotype", "synthetic convulsive episode", "phenotype",
               "reported_result", [("synthetic seizure was observed in all individuals", "RESULTS")]),
    ]},
    "PMID:99000002": {"abstained": False, "abstention_reason": None, "claims": [
        _claim("synthetic disorder B", "disease", "has_phenotype", "synthetic seizure", "phenotype", "reported_result",
               [(POP_B, "RESULTS"), ("synthetic seizure and synthetic ataxia were reported", "RESULTS")], population_text=POP_B),
        _claim("synthetic disorder B", "disease", "has_phenotype", "synthetic ataxia", "phenotype", "reported_result",
               [(POP_B, "RESULTS"), ("synthetic seizure and synthetic ataxia were reported", "RESULTS")], population_text=POP_B),
        _claim("synthetic disorder B", "disease", "has_phenotype", "synthetic hypotonia", "phenotype", "inconclusive",
               [("Whether synthetic hypotonia occurs in synthetic disorder B remains inconclusive.", "CONCLUSIONS")]),
    ]},
    "DOI:10.0000/synthetic.preprint1": {"abstained": False, "abstention_reason": None, "claims": [
        _claim("synthetic disorder A", "disease", "has_phenotype", "synthetic seizure", "phenotype", "planned",
               [("We will record synthetic seizure frequency in 40 participants with synthetic disorder A over 24 months.", "abstract")]),
        _claim("synthetic disorder A", "disease", "has_phenotype", "synthetic seizure", "phenotype", "reported_result",
               [("We will record synthetic seizure frequency in 40 participants with synthetic disorder A over 24 months.", "abstract")]),
    ]},
}
PASS = {"verdict": "pass", "reason": "Synthetic scripted check.", "quotes": []}


def default_check(request: ModelRequest) -> dict:
    proposal = json.loads(request.input_text.split("\n", 2)[1])
    out = {name: dict(PASS) for name in ("text_supports_assertion", "relation_and_direction", "status_and_attribution",
                                         "scope_preserved", "entities_correct")}
    if proposal["predicate"] == "gene_associated_with_disease":
        out["status_and_attribution"] = {"verdict": "fail", "reason": "Synthetic: attributed to earlier reports.",
                                         "quotes": ["in earlier reports"]}
    return out


@dataclass
class World:
    ws: Workspace
    extraction_client: ScriptedClient | None = None
    candidates: list = field(default_factory=list)
    promoted: dict = field(default_factory=dict)

    @property
    def root(self) -> Path:
        return self.ws.root

    def catalog(self):
        return self.ws.catalog()

    def assertion_where(self, **criteria):
        found = [a for a in self.catalog().assertions.values()
                 if all(getattr(a, k) == v for k, v in criteria.items())]
        assert len(found) == 1, (criteria, len(found))
        return found[0]


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def preload_captures(ws: Workspace) -> None:
    fetcher = Fetcher(ws.cache, offline=True)
    for url, backend, name in RECORDED:
        fetcher.import_recorded_capture(url, backend, None, FETCHED_AT, 200, (SYN / name).read_bytes(),
                                        {"content-type": "text/plain"})


def _mechanism_import(ws: Workspace, context_id: str, disease_id: str, gene_id: str, mech_id: str, effect: str,
                      label: str, source_ref: str, quote: str, cell: str | None, assay: str | None,
                      scope_text: str, section: str | None = None) -> MechanismContextImport:
    catalog = ws.catalog()
    source = next(s for s in catalog.sources.values() if s.external_ref == source_ref)
    evidence = text_evidence(source, [(quote, section)], study_design="synthetic laboratory statement")
    scope = Scope(**{**Scope.empty().payload(), "cell_or_tissue": cell, "assay_text": assay})
    assertion = make_assertion(subject_id=disease_id, predicate="disease_has_mechanism", object_id=mech_id,
                               context_id=context_id, scope=scope, effect_direction="not_applicable",
                               statement_status="reported_result", origin="manual_import", support_ids=[])
    support = make_support(assertion.id, evidence.id)
    assertion = make_assertion(subject_id=disease_id, predicate="disease_has_mechanism", object_id=mech_id,
                               context_id=context_id, scope=scope, effect_direction="not_applicable",
                               statement_status="reported_result", origin="manual_import", support_ids=[support.id])
    mechanism = Entity(id=mech_id, type="mechanism", label=label, aliases=[], external_ids=[],
                       properties={"gene_id": gene_id, "functional_effect": effect, "process_ids": [],
                                   "cell_tissue_text": cell, "assay_species_text": assay,
                                   "definition_evidence_ids": [evidence.id]},
                       identity_source_ids=[source.id])
    context = ResearchContext(id=context_id, gene_ids=[gene_id], disease_id=disease_id, mechanism_id=mech_id,
                              profile_level="subgroup", label=label, scope=scope_text,
                              definition_evidence_ids=[evidence.id], definition_assertion_ids=[assertion.id],
                              definition_review_state="pending")
    return MechanismContextImport(context=context, entities=[mechanism], assertions=[assertion], evidence=[evidence],
                                  supports=[support], reviews=[])


def disease_context(cid: str, disease: str, gene: str | None, label: str) -> ResearchContext:
    return ResearchContext(id=cid, gene_ids=[gene] if gene else [], disease_id=disease, mechanism_id=None,
                           profile_level="disease", label=label,
                           scope=f"All people with {label.split(' (')[0]}; mechanism not specified.",
                           definition_evidence_ids=[], definition_assertion_ids=[], definition_review_state="pending")


def audit_rows() -> list[dict]:
    rows = []
    for cid, mondo, exact, rows_n, baseline in [
        ("ctx:syn-a-unknown", "MONDO:9900001", ["DECIPHER:901", "OMIM:900001", "ORPHA:90001"], 6, True),
        ("ctx:syn-a-loss", "MONDO:9900001", ["DECIPHER:901", "OMIM:900001", "ORPHA:90001"], 6, True),
        ("ctx:syn-a-gain", "MONDO:9900001", ["DECIPHER:901", "OMIM:900001", "ORPHA:90001"], 6, True),
        ("ctx:syn-b-unknown", "MONDO:9900002", ["OMIM:900002"], 3, True),
        ("ctx:syn-b-loss", "MONDO:9900002", ["OMIM:900002"], 3, True),
        ("ctx:syn-c-group", "MONDO:9900003", [], 0, False),
        ("ctx:syn-d", "MONDO:9900004", ["ORPHA:90004"], 2, True),
    ]:
        rows.append({"context_id": cid, "intended_population": f"synthetic population for {cid}", "mondo_id": mondo,
                     "source_supported_granularity": "grouping class" if not exact else "disease",
                     "exact_mapped_ids": exact, "hpo_annotation_rows": rows_n, "baseline_available": baseline,
                     "justification_source_ids": ["synthetic-fixture-source"], "audited_by": TEST_REVIEWER})
    return rows


def build_sources_and_backbone(root: Path) -> World:
    ws = Workspace(root)
    for d in (ws.config, ws.imports, ws.review):
        d.mkdir(parents=True, exist_ok=True)
    write_json(ws.config / "targets.json", targets())
    write_json(ws.config / "approved_urls.json", APPROVED)
    write_json(ws.config / "models.json", MODELS)
    write_json(ws.config / "budget.json", {"shared_cap_usd": 0, "providers": {"openai": {"cap_usd": 0},
                                                                           "brightdata": {"max_requests": 0}}})
    write_json(ws.config / "release.json", {
        "published_at": "2026-10-03T00:00:00Z", "title": "Synthetic offline test snapshot",
        "limitations": ["SYNTHETIC TEST DATA: invented records exercising the real pipeline; not research."],
        "example_context_ids": ["ctx:syn-a-unknown", "ctx:syn-a-loss"]})
    write_json(ws.config / "context_mapping_audit.json", {"contexts": audit_rows()})
    (ws.config / "prompts").mkdir(exist_ok=True)
    repo_prompts = Path(__file__).resolve().parents[1] / "config" / "prompts"
    for p in repo_prompts.glob("*.txt"):
        (ws.config / "prompts" / p.name).write_text(p.read_text())
    preload_captures(ws)
    run_sources(ws, targets(), offline=True)
    contexts = [disease_context("ctx:syn-a-unknown", "MONDO:9900001", "HGNC:900001", "Synthetic disorder A (mechanism unknown)"),
                disease_context("ctx:syn-b-unknown", "MONDO:9900002", "HGNC:900002", "Synthetic disorder B (mechanism unknown)"),
                disease_context("ctx:syn-c-group", "MONDO:9900003", None, "Synthetic disorder C grouping (mechanism unknown)"),
                disease_context("ctx:syn-d", "MONDO:9900004", None, "Synthetic disorder D (mechanism unknown)")]
    write_jsonl(ws.imports / "contexts.jsonl", contexts)
    lines = [
        _mechanism_import(ws, "ctx:syn-a-loss", "MONDO:9900001", "HGNC:900001", "mech:syn-a-loss", "loss",
                          "Synthetic disorder A, SYNTHA loss-of-function subgroup", f"{BASE}/gamma-lab",
                          "Synthetic loss-of-function SYNTHA variants reduced synthetic channel current in the same cultured synthetic neurons.",
                          "cultured synthetic neurons", None, "People with synthetic disorder A and SYNTHA loss-of-function variants."),
        _mechanism_import(ws, "ctx:syn-a-gain", "MONDO:9900001", "HGNC:900001", "mech:syn-a-gain", "gain",
                          "Synthetic disorder A, SYNTHA gain-of-function subgroup", f"{BASE}/gamma-lab",
                          "In cultured synthetic neurons, synthetic gain-of-function SYNTHA variants increased synthetic channel current in patch-clamp recordings.",
                          "cultured synthetic neurons", "patch-clamp recordings", "People with synthetic disorder A and SYNTHA gain-of-function variants."),
        _mechanism_import(ws, "ctx:syn-b-loss", "MONDO:9900002", "HGNC:900002", "mech:syn-b-loss", "loss",
                          "Synthetic disorder B, SYNTHB loss-of-function subgroup", "PMID:99000002",
                          "SYNTHB loss-of-function variants", None, None,
                          "People with synthetic disorder B and SYNTHB loss-of-function variants.", "RESULTS"),
    ]
    write_jsonl(ws.imports / "mechanism_contexts.jsonl", lines, sort_key=None)
    alpha = next(s for s in ws.catalog().sources.values() if s.external_ref == f"{BASE}/alpha")
    beta = next(s for s in ws.catalog().sources.values() if s.external_ref == f"{BASE}/beta")

    def ref(doc, quote):
        return {"source_document_id": doc.id, "quotes": [{"quote": quote, "section_label": None}],
                "record_locator": None, "record_fields": None}

    org_rows = [
        {"id": "org:syn-alpha", "name": "Synthetic Alpha Foundation", "official_url": f"{BASE}/alpha",
         "contact_url": f"{BASE}/alpha/contact", "served_disease_ids": ["MONDO:9900001"],
         "serves_evidence": [ref(alpha, "The Synthetic Alpha Foundation supports families affected by synthetic disorder A.")],
         "verified_by": TEST_REVIEWER, "verified_on": REVIEWED_AT},
        {"id": "org:syn-beta", "name": "Synthetic Beta Network", "official_url": f"{BASE}/beta",
         "contact_url": f"{BASE}/beta/contact", "served_disease_ids": ["MONDO:9900002"],
         "serves_evidence": [ref(beta, "The Synthetic Beta Network is a community for synthetic disorder B.")],
         "verified_by": TEST_REVIEWER, "verified_on": REVIEWED_AT}]
    asset_common = {"materials_url": None, "access_terms_text": None, "outcome_measures": [], "age_scope": None,
                    "genotype_scope": None, "reuse_permission": "unknown", "last_checked_at": REVIEWED_AT,
                    "verified_by": TEST_REVIEWER, "verified_on": REVIEWED_AT}
    asset_rows = [
        {"id": "asset:syn-a-registry", "name": "Synthetic A Registry", "kind": "registry",
         "owner_organization_id": "org:syn-alpha", "ownership_relation": "runs",
         "ownership_evidence": [ref(alpha, "The Synthetic A Registry is run by the Synthetic Alpha Foundation.")],
         "url": f"{BASE}/alpha", "target_context_ids": ["ctx:syn-a-unknown"],
         "scope_evidence": [ref(alpha, "Our Synthetic A Registry collects natural-history data from people with synthetic disorder A of all ages.")],
         "population_scope": "people with synthetic disorder A of all ages", **asset_common},
        {"id": "asset:syn-b-questionnaire", "name": "Synthetic Beta Daily Questionnaire", "kind": "questionnaire",
         "owner_organization_id": "org:syn-beta", "ownership_relation": "owns",
         "ownership_evidence": [ref(beta, "The Synthetic Beta Daily Questionnaire, owned by the Synthetic Beta Network, records daily events in children with synthetic disorder B.")],
         "url": f"{BASE}/beta", "target_context_ids": ["ctx:syn-b-unknown"],
         "scope_evidence": [ref(beta, "The Synthetic Beta Daily Questionnaire, owned by the Synthetic Beta Network, records daily events in children with synthetic disorder B.")],
         "population_scope": "children with synthetic disorder B", **asset_common}]
    for name, rows in (("communities", org_rows), ("assets", asset_rows)):
        (ws.imports / f"{name}.jsonl").write_text("".join(canonical_json(r) + "\n" for r in rows))
    run_backbone(ws, targets())
    return World(ws)


def run_literature(world: World, extraction_script=None, check_script=default_check) -> World:
    ws = world.ws
    catalog = ws.catalog()
    cache = ResponseCache(ws.model_dir)
    instructions = (ws.config / "prompts" / "extraction_v1.txt").read_text()
    ex_cfg = StageModelConfig("recorded-synthetic-model", "extraction_v1", instructions, {}, 2000)
    ck_cfg = StageModelConfig("recorded-synthetic-model", "checking_v1",
                              (ws.config / "prompts" / "checking_v1.txt").read_text(), {}, 1000)
    docs_by_hash = {d.canonical_sha256: d for d in catalog.sources.values()}
    script = extraction_script or (lambda r: EXTRACTIONS[docs_by_hash[r.content_hash].external_ref])
    client = ScriptedClient(cache, script)
    index = AliasIndex()
    ontology, _, ids = load_reference(ws)
    index.add_hpo_ontology(ontology, ids[0])
    for e in catalog.entities.values():
        index.add_entity(e)
    entities = OntologyBackedEntities(catalog.entities, ws)
    candidates = []
    for doc in sorted((d for d in catalog.sources.values() if d.source_kind == "literature"), key=lambda d: d.id):
        found, _ = extract_document(doc, client, ex_cfg, index, entities)
        candidates += found
    write_jsonl(ws.stage_file("candidates", "literature"), candidates)
    assignments = {}
    for c in candidates:
        if c.resolution_state != "linked":
            continue
        ref = catalog.sources[c.source_document_id].external_ref
        if ref == "PMID:99000001" and c.predicate == "has_phenotype":
            assignments[c.id] = ContextAssignment(c.id, "ctx:syn-a-loss", "synthetic case series", ["cohort:syn-a-1"],
                                                  "unknown", 12 if c.scope.population_text else None, TEST_REVIEWER)
        elif ref == "PMID:99000002" and c.predicate == "has_phenotype":
            assignments[c.id] = ContextAssignment(c.id, "ctx:syn-b-loss", "synthetic case series", ["cohort:syn-b-1"],
                                                  "unknown", 9 if c.scope.population_text else None, TEST_REVIEWER)
    # A person's assignments are written to the same file the CLI reads.
    write_context_assignments(ws.review / "context_assignments.jsonl", list(assignments.values()))
    assignments = load_context_assignments(ws.review / "context_assignments.jsonl")
    out, promoted = promote(candidates, catalog.sources, assignments)
    from atlas.pipeline import phenotype_entities_for
    out.entities += [e for e in phenotype_entities_for(ws, {a.object_id for a in out.assertions
                                                            if a.object_id.startswith("HP:")}) if e.id not in catalog.entities]
    ws.write_part("literature", out)
    check_client = ScriptedClient(cache, check_script)
    checks = [check_candidate(c, catalog.sources[c.source_document_id], dict(entities) | {e.id: e for e in out.entities},
                              check_client, ck_cfg) for c in candidates if c.id in promoted]
    write_jsonl(ws.stage_file("checks", "literature"), checks)
    (ws.stage / "literature" / "promoted.json").write_text(json.dumps(promoted, indent=2, sort_keys=True))
    world.extraction_client = client
    world.candidates = candidates
    world.promoted = promoted
    return world


def accept_pending(world: World, *, skip: Callable[[dict], bool] = lambda row: False,
                   decision: str = "accepted", expert: bool = False, disagreement: str | None = None):
    """Simulate a reviewer filling the exported template (synthetic stand-in decisions)."""
    ws = world.ws
    catalog = ws.catalog()
    from atlas.cli import _all_reviews

    ontology, _, _ = load_reference(ws)
    structural = structural_report(catalog, ontology)
    export_queue(catalog, _all_reviews(ws), structural, ws.checks(), ws.review)
    rows = []
    for line in (ws.review / "decisions.template.jsonl").read_text().splitlines():
        row = json.loads(line)
        if skip(row):
            continue
        dims = {k: "pass" for k in ("source_fidelity", "entity_identity", "direction", "statement_status", "scope",
                                    "applicability")}
        dims |= {"expert_validation": expert, "compatibility_qualified_review": False,
                 "model_check_disagreement": disagreement}
        row |= {"reviewer": TEST_REVIEWER, "reviewed_at": REVIEWED_AT, "decision": decision,
                "reason": "Synthetic stand-in decision for an automated test.", "dimensions": dims}
        rows.append(row)
    path = ws.review / "decisions.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    report = import_decisions(path, catalog, structural, ws.checks())
    from atlas.models.io import read_jsonl
    from atlas.models.records import Review
    existing = {r.id: r for r in read_jsonl(ws.review / "reviews.jsonl", Review)}
    existing.update({r.id: r for r in report.imported})
    write_jsonl(ws.review / "reviews.jsonl", existing.values())
    return report


def add_opportunity_gap_explanation(world: World, comparison_id: str) -> None:
    ws = world.ws
    catalog = ws.catalog()
    beta_owner = world.assertion_where(predicate="owns_or_runs", object_id="asset:syn-b-questionnaire")
    beta_scope = world.assertion_where(predicate="asset_for_context", subject_id="asset:syn-b-questionnaire")
    opp = OpportunityRecord(
        id="opp:syn-a-loss-beta-questionnaire", starting_context_id="ctx:syn-a-loss",
        partner_entity_ids=["org:syn-beta"], asset_id="asset:syn-b-questionnaire", kind="measurement",
        readiness="investigate_compatibility", basis_assertion_ids=sorted([beta_owner.id, beta_scope.id]),
        comparison_ids=[comparison_id], known_differences=["The questionnaire was written for children with synthetic disorder B."],
        unknowns=["Whether materials can be shared, and whether its items apply to synthetic disorder A, are unknown."],
        expert_checks=["Ask the owner what may be shared.", "Have a qualified expert assess measurement compatibility."],
        action_text="Ask the Synthetic Beta Network whether its questionnaire could be inspected for a compatibility review.",
        outreach_draft="SYNTHETIC TEST DRAFT: Could we discuss whether your questionnaire's access terms permit a compatibility assessment? Nothing is sent.",
        contact_url=f"{BASE}/beta/contact", review_state="pending", review_id=None)
    write_jsonl(ws.imports / "opportunities.jsonl", [opp])
    failed = next(c for c in ws.coverage() if f"{BASE}/gamma-community" in c.query_or_urls)
    gap = GapRecord(id="gap:syn-c-community", context_id="ctx:syn-c-group", kind="incomplete_retrieval",
                    coverage_record_ids=[failed.id],
                    description="No community resource was found in these inspected sources; one approved page could not be captured.",
                    evidence_needed=["A captured official community page for synthetic disorder C."],
                    next_question="Which official community pages for synthetic disorder C should be captured next?")
    write_jsonl(ws.imports / "gaps.jsonl", [gap])
    seizure = world.assertion_where(predicate="has_phenotype", context_id="ctx:syn-a-loss", object_id="HP:9000011")
    hypotonia = world.assertion_where(predicate="has_phenotype", context_id="ctx:syn-a-loss", object_id="HP:9000014")
    ids = sorted([seizure.id, hypotonia.id])
    explanation = Explanation(
        id="expl:syn-a-loss-summary", context_id="ctx:syn-a-loss",
        sentences=[SentenceRecord(text="One synthetic case series reports synthetic seizure and synthetic hypotonia in this subgroup.",
                                  assertion_ids=ids, calculation_ids=[], opportunity_ids=[])],
        dependency_hashes={a: fingerprint(catalog, "assertion", a) for a in ids}, origin="human_authored",
        model_response_id=None, prompt_version=None, policy_version="explanation-policy-1", review_state="pending",
        review_id=None)
    write_jsonl(ws.imports / "explanations.jsonl", [explanation])


def build_full_world(root: Path) -> tuple[World, dict, Any]:
    """Sources -> backbone -> literature -> review -> assemble -> opportunity/gap/explanation -> re-assemble."""
    from atlas.assembly.assemble import assemble
    from atlas.cli import assembly_inputs

    world = build_sources_and_backbone(root)
    run_literature(world)
    accept_pending(world, disagreement=None)
    # The background claim's model check failed; a reviewer accepting it must record a disagreement reason.
    accept_pending(world, disagreement="Synthetic reviewer: background attribution is reflected in the status.")
    package, _ = assemble(assembly_inputs(world.ws))
    comparison = next(c for c in package["content"]["comparisons"]
                      if c["context_a"] == "ctx:syn-a-loss" and c["context_b"] == "ctx:syn-b-loss")
    add_opportunity_gap_explanation(world, comparison["id"])
    accept_pending(world)
    package, report = assemble(assembly_inputs(world.ws))
    return world, package, report
