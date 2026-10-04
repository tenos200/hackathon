"""Identity, linking and source adapters with synthetic source-format fixtures (offline-unit)."""

from __future__ import annotations

import json

import httpx
import pytest

from atlas.linking.index import AliasIndex
from atlas.models.records import Entity
from atlas.sources import adapters
from atlas.sources.community import AssetImport
from atlas.sources.fetch import Capture, FetchError, Fetcher, load_approved_pages
from atlas.sources.hpo import HpoOntology, normalize_disease_namespace, parse_hpoa
from atlas.sources.mondo import exact_annotation_ids, parse_sssom
from atlas.workspace import Workspace

from .conftest import real_store_for
from .synthetic_world import BASE, SYN, build_sources_and_backbone


def _ontology() -> HpoOntology:
    return HpoOntology.from_obo((SYN / "hp_synthetic.obo").read_text(), "test")


@pytest.mark.master("T01", boundary="offline-unit")
def test_ambiguous_alias_returns_all_candidates_and_type_numbers_stay_distinct(tmp_path, world):
    w, package, _ = world
    index = AliasIndex()
    for entity in w.catalog().entities.values():
        index.add_entity(entity)
    shared = index.lookup("SYNA1", "gene")
    assert shared.resolved_id is None and shared.ambiguous
    assert set(shared.candidates) == {"HGNC:900001", "HGNC:900002"}
    assert index.lookup("SYNTHA", "gene").resolved_id == "HGNC:900001"
    six_a = index.lookup("synthetic disorder type 6A", "disease").resolved_id
    six_b = index.lookup("synthetic disorder type 6B", "disease").resolved_id
    assert six_a == "MONDO:9900061" and six_b == "MONDO:9900062"
    broad = index.lookup("synthetic disorder", "disease")  # BROAD synonym of two diseases
    assert broad.resolved_id is None and set(broad.candidates) == {"MONDO:9900001", "MONDO:9900002"}
    # The extraction candidate that used the shared alias is held back, not guessed.
    ambiguous = [c for c in w.candidates if c.subject_mention == "SYNA1"]
    assert ambiguous and ambiguous[0].resolution_state == "ambiguous" and "ambiguous" in ambiguous[0].reason
    # Public search flags the ambiguity on every match.
    store = real_store_for(package)
    matches = store.search("SYNA1").data.matches
    assert {m.id for m in matches} == {"HGNC:900001", "HGNC:900002"} and all(m.ambiguous for m in matches)


@pytest.mark.master("T01", boundary="offline-unit")
@pytest.mark.master("T27", boundary="offline-unit")
def test_related_and_broad_mappings_never_merge_or_supply_a_baseline(world):
    w, package, _ = world
    catalog = w.catalog()
    disease_b = catalog.entities["MONDO:9900002"]
    assert "OMIM:900006" not in disease_b.external_ids  # relatedMatch stays a typed link
    related = [m for m in catalog.mappings.values() if m.target_id == "OMIM:900006"]
    assert related and related[0].relation == "related"
    store = real_store_for(package)
    b_terms = {t.id for t in store.context("ctx:syn-b-unknown").data.disease_profile.terms}
    assert "HP:9000013" not in b_terms  # OMIM:900006's annotation never leaks in
    c_profile = store.context("ctx:syn-c-group").data.disease_profile  # broad/close mappings only
    assert c_profile.terms == [] and c_profile.mapped_annotation_ids == []


@pytest.mark.master("T27", boundary="offline-unit")
def test_exact_mappings_union_rows_with_provenance_and_normalize_namespaces(world):
    w, package, _ = world
    _, rows = parse_sssom((SYN / "mondo_synthetic.sssom.tsv").read_text())
    exact = [r.object_id for r in exact_annotation_ids("MONDO:9900001", rows)]
    assert exact == ["DECIPHER:901", "OMIM:900001", "ORPHA:90001"]  # Orphanet: normalized to ORPHA:
    assert [r.object_id for r in exact_annotation_ids("MONDO:9900005", rows)] == ["OMIM:900011"]  # MIM:
    assert normalize_disease_namespace("ORPHANET:1") == "ORPHA:1" and normalize_disease_namespace("MIM:2") == "OMIM:2"
    store = real_store_for(package)
    profile = store.context("ctx:syn-a-unknown").data.disease_profile
    # DECIPHER:901 is exact but has no rows, so it contributes nothing.
    assert profile.mapped_annotation_ids == ["OMIM:900001", "ORPHA:90001"]
    assert [t.id for t in profile.terms].count("HP:9000011") == 1  # the duplicate term counts once
    seizure = next(t for t in profile.terms if t.id == "HP:9000011")
    # The OMIM row carries a frequency qualifier, so it stays a separately scoped assertion;
    # both original rows (one per mapped ID) remain inspectable.
    rows = {e.record_locator: e for a in seizure.assertion_ids for e in store.assertion(a).data.evidence}
    assert len(rows) == 2 and all(l.startswith("phenotype.hpoa:line:") for l in rows)
    ids = {dict((f.name, f.value) for f in e.record_fields)["database_id"] for e in rows.values()}
    assert ids == {"OMIM:900001", "ORPHA:90001"}
    assert profile.direct_term_count == 3  # seizure, developmental delay, hypotonia; NOT ataxia excluded


@pytest.mark.master("T36", boundary="offline-unit")
def test_full_ontology_alias_index_and_typed_synonyms():
    ontology = _ontology()
    index = AliasIndex()
    index.add_hpo_ontology(ontology, "src:hpo")
    # Not annotated to any selected disease, still resolvable from the full pinned ontology.
    assert index.lookup("Synthetic gaze palsy", "phenotype").resolved_id == "HP:9000050"
    assert index.lookup("Synthetic fit", "phenotype").resolved_id == "HP:9000011"  # EXACT synonym
    assert index.lookup("HP:9000099", "phenotype").resolved_id == "HP:9000011"   # alternate ID
    related = index.lookup("Synthetic convulsive episode", "phenotype")
    assert related.resolved_id is None and related.candidates == ("HP:9000011",) and related.via == "candidate_alias"
    narrow = index.lookup("Synthetic eye movement limitation", "phenotype")
    assert narrow.resolved_id is None and narrow.via == "candidate_alias"
    assert ontology.resolve("HP:9000030") == "HP:9000011"  # unambiguous replacement
    assert ontology.resolve("HP:9000031") is None        # obsolete without replacement stays unresolved


@pytest.mark.master("T36", boundary="offline-unit")
def test_related_synonym_mention_is_not_auto_linked(world):
    w, _, _ = world
    candidate = next(c for c in w.candidates if c.object_mention == "synthetic convulsive episode")
    assert candidate.resolution_state == "unlinked" and candidate.linked_object_id is None
    assert "non-identity aliases" in candidate.reason


@pytest.mark.master("T02", boundary="offline-unit")
def test_gene_search_offers_contexts_without_inferring_a_mechanism(world):
    _, package, _ = world
    store = real_store_for(package)
    gene = next(m for m in store.search("SYNTHA").data.matches if m.id == "HGNC:900001")
    options = {c.id: c for c in gene.contexts}
    assert set(options) == {"ctx:syn-a-unknown", "ctx:syn-a-loss", "ctx:syn-a-gain"}
    assert options["ctx:syn-a-unknown"].mechanism_known is False  # unknown mechanism is a valid choice
    unknown = store.context("ctx:syn-a-unknown").data
    assert unknown.mechanism is None and any("Mechanism is unknown" in l for l in unknown.limitations)
    assert store.connections("ctx:syn-a-unknown").data.comparisons  # still usable for the journey


@pytest.mark.master("T14", boundary="offline-unit")
def test_source_predicate_fidelity(tmp_path):
    document = adapters.make_document(
        source_kind="curation_database", external_ref="GO:synthetic.gaf", title="synthetic GAF", url=None,
        release_or_version="synthetic", published_at=None, fetched_at="2026-10-03T00:00:00Z", raw_sha256="x",
        canonical_text="", canonicalizer_version="tsv-text-1", sections=[], public_text_policy="full",
        origin="manual_import", metadata=adapters.source_metadata())
    gaf = ["!gaf-version: 2.2",
           "UniProtKB\tSYN1\tSYNTHA\tinvolved_in\tGO:9999001\tPMID:1\tIDA\t\tP\tsynthetic\t\tprotein\ttaxon:9606\t20260101\tSYN\t\t",
           "UniProtKB\tSYN1\tSYNTHA\tNOT|involved_in\tGO:9999002\tPMID:1\tIDA\t\tP\tsynthetic\t\tprotein\ttaxon:9606\t20260101\tSYN\t\t"]
    out = adapters.go_annotations(document, gaf, {"SYNTHA": "HGNC:900001"}, {"GO:9999001", "GO:9999002"})
    assert {a.predicate for a in out.assertions} == {"gene_involved_in_process"}  # never disruption
    statuses = {a.object_id: a.statement_status for a in out.assertions}
    assert statuses == {"GO:9999001": "listed_record", "GO:9999002": "negated"}
    assert all("GO qualifier" in a.scope.other_qualifiers[0] for a in out.assertions)
    # Trial registration is a `studies` listed record, never efficacy.
    capture = Capture("u", "u", "direct", None, 200, {}, "2026-10-03T00:00:00Z", "h", "raw/h")
    trial = adapters.clinical_trial(capture, (SYN / "ctgov_NCT99999901.json").read_bytes(),
                                    {"Synthetic Disorder A": "MONDO:9900001"})
    assert [(a.predicate, a.statement_status) for a in trial.assertions] == [("studies", "listed_record")]
    # A website mention without ownership evidence cannot become ownership.
    with pytest.raises(ValueError, match="ownership"):
        AssetImport.model_validate({
            "id": "asset:x", "name": "x", "kind": "registry", "owner_organization_id": "org:x",
            "ownership_relation": "owns", "ownership_evidence": [], "url": None, "target_context_ids": [],
            "scope_evidence": [], "materials_url": None, "access_terms_text": None, "population_scope": "p",
            "outcome_measures": [], "age_scope": None, "genotype_scope": None, "reuse_permission": "unknown",
            "last_checked_at": "2026-10-03T00:00:00Z", "verified_by": "x", "verified_on": "2026-10-03T00:00:00Z"})
    from atlas.models.predicates import check_predicate
    gene = Entity(id="HGNC:1", type="gene", label="G", aliases=[], external_ids=[], identity_source_ids=[],
                  properties={"symbol": "G", "hgnc_id": "HGNC:1", "gene_family": None})
    disease = Entity(id="MONDO:1", type="disease", label="D", aliases=[], external_ids=[], identity_source_ids=[],
                     properties={"mondo_id": "MONDO:1", "definition": None})
    assert check_predicate("mechanism_causes_disease", gene, disease, "not_applicable", None)  # association != causal
    assert check_predicate("made_up_predicate", gene, disease, "not_applicable", None)


@pytest.mark.master("T07", boundary="offline-unit")
def test_native_validity_is_preserved_and_never_positive():
    document = adapters.make_document(
        source_kind="curation_database", external_ref="GENCC:synthetic", title="synthetic GenCC export", url=None,
        release_or_version="synthetic", published_at=None, fetched_at="2026-10-03T00:00:00Z", raw_sha256="x",
        canonical_text="", canonicalizer_version="tsv-text-1", sections=[], public_text_policy="full",
        origin="manual_import", metadata=adapters.source_metadata())
    rows = [{"gene_curie": "HGNC:900001", "disease_curie": "MONDO:9900001", "classification_title": "Refuted Evidence",
             "submitter_title": "Synthetic Submitter", "moi_title": "Autosomal dominant"}]
    out = adapters.gencc_rows(document, rows, {"HGNC:900001": "HGNC:900001"}, {"MONDO:9900001": "MONDO:9900001"})
    evidence = out.evidence[0]
    assert evidence.source_native_validity == "Refuted Evidence"  # verbatim
    assert out.assertions[0].statement_status == "listed_record"
    from atlas.assembly.assemble import has_non_positive_validity
    assert has_non_positive_validity([evidence])


@pytest.mark.master("T30", boundary="offline-unit")
def test_recorded_direct_and_brightdata_captures_keep_backend_provenance(world):
    w, package, _ = world
    sources = {s.external_ref: s for s in w.catalog().sources.values()}
    assert sources[f"{BASE}/alpha"].source_metadata["fetch_backend"] == "direct"
    assert sources[f"{BASE}/beta"].source_metadata["fetch_backend"] == "brightdata"
    assert sources[f"{BASE}/beta"].source_metadata["original_target_url"] == f"{BASE}/beta"
    # Missing Bright Data credentials did not block direct or cached sources; the uncaptured page is a failure record.
    failed = [c for c in w.ws.coverage() if c.completion == "failed"]
    assert any(f"{BASE}/gamma-community" in c.query_or_urls for c in failed)


def _transport(handler):
    return lambda: httpx.Client(transport=httpx.MockTransport(handler))


@pytest.mark.master("T30", boundary="offline-unit")
def test_capture_backends_reject_target_errors_and_follow_documented_brightdata_request(tmp_path):
    pages = tmp_path / "approved.json"
    pages.write_text(json.dumps({"pages": [
        {"url": "https://org.invalid/page", "fetch_backend": "brightdata", "transformation": "markdown",
         "expect_text": "Real Org", "purpose": "t"},
        {"url": "https://org.invalid/direct", "fetch_backend": "direct", "transformation": None,
         "expect_text": "Real Org", "purpose": "t"}]}))
    seen = []

    def handler(request: httpx.Request):
        seen.append(request)
        if request.url.host == "api.brightdata.com":
            body = json.loads(request.content)
            assert body == {"zone": "zone1", "url": "https://org.invalid/page", "format": "raw", "data_format": "markdown"}
            assert request.headers["authorization"] == "Bearer key"
            return httpx.Response(200, text="# Access denied\nPlease enable cookies")  # proxy OK, target page wrong
        return httpx.Response(404, text="not found")

    class Ledger:
        def reserve_request(self, provider, purpose):
            return "r"

        def settle(self, reservation, outcome, actual_usd=None):
            pass

    fetcher = Fetcher(tmp_path / "cache", offline=False, approved_pages=load_approved_pages(pages),
                      client_factory=_transport(handler), brightdata_key="key", brightdata_zone="zone1",
                      brightdata_ledger=Ledger(), sleep=lambda s: None)
    with pytest.raises(FetchError, match="expected marker"):
        fetcher.get("https://org.invalid/page")
    with pytest.raises(FetchError, match="HTTP 404"):
        fetcher.get("https://org.invalid/direct")
    with pytest.raises(FetchError, match="not on the approved list"):
        fetcher.get("https://model-suggested.invalid/x")
    no_creds = Fetcher(tmp_path / "cache2", offline=False, approved_pages=load_approved_pages(pages),
                       client_factory=_transport(handler), sleep=lambda s: None)
    with pytest.raises(FetchError, match="BRIGHTDATA_API_KEY"):
        no_creds.get("https://org.invalid/page")
    assert not (tmp_path / "cache" / "index.jsonl").exists() or not (tmp_path / "cache" / "index.jsonl").read_text()


@pytest.mark.master("T16", boundary="offline-unit")
def test_transient_failures_retry_at_most_twice_then_record_failure(tmp_path):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503)

    fetcher = Fetcher(tmp_path / "c", offline=False, api_prefixes=("https://api.invalid/",),
                      client_factory=_transport(handler), sleep=lambda s: None)
    with pytest.raises(FetchError, match="failed after 2 retries"):
        fetcher.get("https://api.invalid/x")
    assert len(calls) == 3
    offline = Fetcher(tmp_path / "c", offline=True, api_prefixes=("https://api.invalid/",))
    with pytest.raises(FetchError, match="offline"):
        offline.get("https://api.invalid/y")


@pytest.mark.master("T31", boundary="offline-unit")
def test_selected_preprint_pins_version_and_keeps_preprint_metadata(world):
    w, package, _ = world
    source = next(s for s in w.catalog().sources.values() if s.external_ref == "DOI:10.0000/synthetic.preprint1")
    meta = source.source_metadata
    assert source.release_or_version == "1" and meta["manuscript_version"] == "1"
    assert meta["published_doi"] is None and meta["original_fields"]["published"] == "NA"  # sentinel normalized, original kept
    assert meta["license"] == "cc_by" and meta["publication_status"] == "preprint"
    assert meta["publication_family_id"] == "family:doi:10.0000/synthetic.preprint1"
    assert source.public_text_policy == "link_only"
    # The protocol's claim stays planned; a 'reported_result' reading of it is blocked structurally.
    published = {a.record.statement_status for a in real_store_for(package).assertions.values()
                 if a.record.subject_id == "MONDO:9900001" and a.record.object_id == "HP:9000011"
                 and any(real_store_for(package).evidence[real_store_for(package).supports[s].evidence_id].source_id == source.id
                         for s in a.record.support_ids)}
    assert published == {"planned"}


@pytest.mark.master("T31", boundary="offline-unit")
def test_later_article_link_does_not_relabel_or_count_as_independent():
    capture = Capture("u", "u", "direct", None, 200, {}, "2026-10-03T00:00:00Z", "h", "raw/h")
    body = (SYN / "medrxiv_synthetic.json").read_bytes()
    v2 = adapters.medrxiv_preprint(capture, body, "10.0000/synthetic.preprint1", "2", public_text_policy="excerpt",
                                   publication_family_id=None, document_role="protocol").documents[0]
    v1 = adapters.medrxiv_preprint(capture, body, "10.0000/synthetic.preprint1", "1", public_text_policy="excerpt",
                                   publication_family_id=None, document_role="protocol").documents[0]
    assert v2.source_metadata["published_doi"] == "10.0000/synthetic.article1"
    assert v2.source_metadata["publication_status"] == "preprint"  # the captured source stays a preprint
    assert v1.id != v2.id  # a different version is a different source, not an overwrite
    assert v1.source_metadata["publication_family_id"] == v2.source_metadata["publication_family_id"]
    from atlas.analytics.phenotype import merge_shared_cohorts
    assert len(merge_shared_cohorts({v1.source_metadata["publication_family_id"]: set()})) == 1
    smoke = json.loads((SYN.parents[2] / "contracts" / "medrxiv_smoke_manifest.json").read_text())
    assert smoke["http_status"] == 200 and smoke["collection_count"] == 1  # the recorded real route check
    missing = adapters.medrxiv_preprint(capture, body, "10.0000/synthetic.preprint1", "9", public_text_policy="excerpt",
                                        publication_family_id=None, document_role="protocol")
    assert not missing.documents and missing.coverage[0].completion == "partial"


@pytest.mark.master("T11", boundary="offline-unit")
def test_hpoa_parser_keeps_not_rows_and_qualifiers():
    annotations = parse_hpoa((SYN / "phenotype_synthetic.hpoa").read_text(), "h")
    assert annotations.release == "synthetic-2026-10-01"
    not_rows = [r for r in annotations.rows if r.negated]
    assert {(r.database_id, r.hpo_id) for r in not_rows} == {("OMIM:900001", "HP:9000015"), ("OMIM:900011", "HP:9000014")}
    seizure = next(r for r in annotations.rows if r.database_id == "OMIM:900001" and r.hpo_id == "HP:9000011")
    assert seizure.frequency == "HP:0040283" and seizure.evidence == "TAS" and seizure.reference == "OMIM:900001"


def test_sources_stage_offline_reproduces_identical_records(tmp_path):
    first = build_sources_and_backbone(tmp_path / "a")
    second = build_sources_and_backbone(tmp_path / "b")
    a = Workspace(first.root).catalog()
    b = Workspace(second.root).catalog()
    assert set(a.sources) == set(b.sources) and set(a.assertions) == set(b.assertions)


@pytest.mark.master("T07", boundary="offline-unit")
def test_refuted_classification_is_published_verbatim_not_as_established(tmp_path):
    from atlas.assembly.assemble import assemble
    from atlas.cli import assembly_inputs
    from atlas.models.io import read_jsonl, write_jsonl
    from atlas.models.records import Assertion, Evidence, SourceDocument, Support
    from atlas.sources.canonical import canonicalize_text

    from .synthetic_world import accept_pending

    world = build_sources_and_backbone(tmp_path / "ws")
    header = "gene_curie\tdisease_curie\tclassification_title\tsubmitter_title\tmoi_title"
    row = "HGNC:900001\tMONDO:9900001\tRefuted Evidence\tSynthetic Submitter\tAutosomal dominant"
    text = canonicalize_text(f"{header}\n{row}\n")
    document = adapters.make_document(
        source_kind="curation_database", external_ref="GENCC:synthetic-export", title="Synthetic GenCC export",
        url=None, release_or_version="synthetic-2026-10-01", published_at=None, fetched_at="2026-10-03T00:00:00Z",
        raw_sha256="synthetic", canonical_text=text, canonicalizer_version="tsv-text-1", sections=[],
        public_text_policy="full", origin="manual_import", metadata=adapters.source_metadata())
    out = adapters.gencc_rows(document, [dict(zip(header.split("\t"), row.split("\t")))],
                              {"HGNC:900001": "HGNC:900001"}, {"MONDO:9900001": "MONDO:9900001"})
    ws = world.ws
    for kind, model, extra in (("documents", SourceDocument, [document]), ("evidence", Evidence, out.evidence),
                               ("assertions", Assertion, out.assertions), ("supports", Support, out.supports)):
        path = ws.stage_file(kind, "backbone")
        write_jsonl(path, [*read_jsonl(path, model), *extra])
    accept_pending(world)
    package, _ = assemble(assembly_inputs(ws))
    view = real_store_for(package).assertion(out.assertions[0].id).data
    assert view.statement_status == "listed_record" and view.predicate == "gene_associated_with_disease"
    assert "Source classification: Refuted Evidence" in {b.label for b in view.badges}
    assert view.evidence[0].source_native_validity == "Refuted Evidence"
    assert not any("established" in b.label.lower() for b in view.badges)
