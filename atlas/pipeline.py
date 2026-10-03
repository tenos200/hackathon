"""Stage orchestration used by the CLI (pipeline only; never imported by the API).

Stage 1 `sources`: capture pinned sources from config, canonicalize once, resolve
exact identities, keep typed mappings, record coverage. Stage 2 `backbone`:
structured candidate assertions (HPO disease rows through exact mappings) and
the manual community/asset/study/person imports. A failed capture becomes an
incomplete coverage record, never an empty source or a guessed record.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from atlas.budget import BudgetLedger
from atlas.models.io import read_jsonl, write_jsonl
from atlas.models.records import Entity, ResearchContext, SourceDocument
from atlas.sources import adapters
from atlas.sources.builders import AdapterOutput, make_document, source_metadata
from atlas.sources.canonical import TEXT_CANONICALIZER, canonicalize_text
from atlas.sources.community import (
    AssetImport, OrganizationImport, PersonImport, StudyImport, normalize_imports, web_page,
)
from atlas.sources.fetch import Capture, FetchError, Fetcher, load_approved_pages, utc_now
from atlas.sources.hpo import HpoOntology, IcReference, build_ic_reference, parse_hpoa
from atlas.sources.mondo import parse_sssom
from atlas.sources.obo import parse_obo
from atlas.workspace import Workspace

EUTILS_EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
HGNC_FETCH = "https://rest.genenames.org/fetch/symbol/"
MEDRXIV_DETAILS = "https://api.medrxiv.org/details/medrxiv/"
CTGOV_STUDY = "https://clinicaltrials.gov/api/v2/studies/"


def make_fetcher(ws: Workspace, targets: dict[str, Any], offline: bool) -> Fetcher:
    pinned_prefixes = tuple(u for section in ("hpo", "mondo") for k, u in (targets.get(section) or {}).items()
                            if k.endswith("_url") and u)
    ledger = None
    budget_path = ws.config / "budget.json"
    if budget_path.exists():
        ledger = BudgetLedger.from_files(ws.root / "work" / "budget_ledger.jsonl", budget_path)
    interval = 0.11 if os.environ.get("NCBI_API_KEY") else 0.34  # documented 10/s with key, 3/s without
    return Fetcher(
        ws.cache, offline=offline,
        api_prefixes=(EUTILS_EFETCH, HGNC_FETCH, MEDRXIV_DETAILS, CTGOV_STUDY, *pinned_prefixes),
        approved_pages=load_approved_pages(ws.config / "approved_urls.json"),
        min_interval_seconds={"eutils.ncbi.nlm.nih.gov": interval, "rest.genenames.org": 0.2,
                              "api.medrxiv.org": 0.5, "clinicaltrials.gov": 0.5},
        brightdata_key=os.environ.get("BRIGHTDATA_API_KEY"), brightdata_zone=os.environ.get("BRIGHTDATA_UNLOCKER_ZONE"),
        brightdata_ledger=ledger)


def _failed(out: AdapterOutput, source: str, url: str, error: FetchError) -> None:
    out.coverage.append(adapters.coverage(source, [url], utc_now(), returned=0, inspected=0, completion="failed",
                                          failure_reason=error.reason))


@dataclass
class HpoBundle:
    ontology: HpoOntology
    reference: IcReference | None
    obo_document: SourceDocument
    hpoa_document: SourceDocument | None
    annotations: Any


def _ontology_document(capture: Capture, text: str, title: str, release: str | None) -> SourceDocument:
    return make_document(
        source_kind="ontology", external_ref=capture.target_url, title=title, url=capture.target_url,
        release_or_version=release, published_at=None, fetched_at=capture.fetched_at, raw_sha256=capture.raw_sha256,
        canonical_text=canonicalize_text(text), canonicalizer_version=TEXT_CANONICALIZER, sections=[],
        public_text_policy="link_only", origin="download",
        metadata=source_metadata(fetch_backend=capture.backend, original_target_url=capture.target_url))


def load_hpo(ws: Workspace, fetcher: Fetcher, targets: dict[str, Any], out: AdapterOutput) -> HpoBundle | None:
    cfg = targets.get("hpo") or {}
    if not cfg.get("obo_url") or not cfg.get("release"):
        out.coverage.append(adapters.coverage("HPO ontology", ["config: hpo.obo_url/release"], utc_now(), returned=0,
                                              inspected=0, completion="failed",
                                              failure_reason="no pinned HPO release configured"))
        return None
    try:
        obo_cap = fetcher.get(cfg["obo_url"])
    except FetchError as exc:
        _failed(out, "HPO ontology", cfg["obo_url"], exc)
        return None
    obo_text = obo_cap.body(ws.cache).decode("utf-8")
    ontology = HpoOntology.from_obo(obo_text, obo_cap.raw_sha256)
    if ontology.release and cfg["release"] not in ontology.release:
        raise ValueError(f"HPO data-version {ontology.release} does not match pinned release {cfg['release']}")
    obo_doc = _ontology_document(obo_cap, obo_text, "Human Phenotype Ontology (hp.obo)", ontology.release)
    out.documents.append(obo_doc)
    reference, hpoa_doc, annotations = None, None, None
    if cfg.get("hpoa_url"):
        try:
            hpoa_cap = fetcher.get(cfg["hpoa_url"])
            raw = hpoa_cap.body(ws.cache).decode("utf-8")
            annotations = parse_hpoa(raw, hpoa_cap.raw_sha256)
            hpoa_doc = adapters.hpoa_document(annotations, raw, hpoa_cap)
            out.documents.append(hpoa_doc)
            reference = build_ic_reference(annotations, ontology)
        except FetchError as exc:
            _failed(out, "HPO annotations", cfg["hpoa_url"], exc)
    return HpoBundle(ontology, reference, obo_doc, hpoa_doc, annotations)


def write_reference_pointer(ws: Workspace, bundle: HpoBundle | None) -> None:
    ws.stage.mkdir(parents=True, exist_ok=True)
    pointer = None
    if bundle is not None:
        pointer = {"obo_raw_sha256": bundle.obo_document.raw_sha256, "obo_document_id": bundle.obo_document.id,
                   "hpoa_raw_sha256": bundle.hpoa_document.raw_sha256 if bundle.hpoa_document else None,
                   "hpoa_document_id": bundle.hpoa_document.id if bundle.hpoa_document else None}
    (ws.stage / "hpo_reference.json").write_text(json.dumps(pointer, indent=2, sort_keys=True))


_REFERENCE_CACHE: dict[str, tuple[HpoOntology, IcReference | None, list[str]]] = {}


def load_reference(ws: Workspace) -> tuple[HpoOntology, IcReference | None, list[str]]:
    """Ontology + IC reference from cached raw bytes recorded by the sources stage (memoized by content)."""
    path = ws.stage / "hpo_reference.json"
    pointer = json.loads(path.read_text()) if path.exists() else None
    key = json.dumps(pointer, sort_keys=True)
    if key not in _REFERENCE_CACHE:
        _REFERENCE_CACHE[key] = _load_reference(ws, pointer)
    return _REFERENCE_CACHE[key]


def _load_reference(ws: Workspace, pointer: dict | None) -> tuple[HpoOntology, IcReference | None, list[str]]:
    if not pointer:
        return HpoOntology.from_obo("", "none"), None, []
    raw = (ws.cache / "raw" / pointer["obo_raw_sha256"]).read_bytes()
    if pointer.get("obo_format") == "obographs":
        from atlas.sources.bulk import parse_obographs

        ontology = HpoOntology.from_document(parse_obographs(raw), pointer["obo_raw_sha256"])
    else:
        ontology = HpoOntology.from_obo(raw.decode("utf-8"), pointer["obo_raw_sha256"])
    if not pointer.get("hpoa_raw_sha256"):
        return ontology, None, [pointer["obo_document_id"]]
    hpoa = parse_hpoa((ws.cache / "raw" / pointer["hpoa_raw_sha256"]).read_text(encoding="utf-8"),
                      pointer["hpoa_raw_sha256"])
    return ontology, build_ic_reference(hpoa, ontology), [pointer["obo_document_id"], pointer["hpoa_document_id"]]


def run_sources(ws: Workspace, targets: dict[str, Any], offline: bool) -> AdapterOutput:
    fetcher = make_fetcher(ws, targets, offline)
    out = AdapterOutput()
    bundle = load_hpo(ws, fetcher, targets, out)
    write_reference_pointer(ws, bundle)

    # Mondo labels and typed mappings for the selected (audited) disease IDs only.
    mondo_cfg = targets.get("mondo") or {}
    diseases = targets.get("diseases", [])
    if diseases and mondo_cfg.get("obo_url") and mondo_cfg.get("sssom_url") and mondo_cfg.get("release"):
        try:
            obo_cap = fetcher.get(mondo_cfg["obo_url"])
            sssom_cap = fetcher.get(mondo_cfg["sssom_url"])
            obo_text = obo_cap.body(ws.cache).decode("utf-8")
            sssom_text = sssom_cap.body(ws.cache).decode("utf-8")
            mondo = parse_obo(obo_text)
            if mondo.data_version and mondo_cfg["release"] not in mondo.data_version:
                raise ValueError(f"Mondo data-version {mondo.data_version} does not match pin {mondo_cfg['release']}")
            obo_doc = _ontology_document(obo_cap, obo_text, "Mondo disease ontology (mondo.obo)", mondo.data_version)
            sssom_doc = _ontology_document(sssom_cap, sssom_text, "Mondo mappings (SSSOM)", mondo_cfg["release"])
            out.documents += [obo_doc, sssom_doc]
            _, rows = parse_sssom(sssom_text)
            for mondo_id in diseases:
                try:
                    entity, mappings = adapters.mondo_disease(mondo_id, mondo, obo_doc.id, rows, sssom_doc.id)
                except KeyError as exc:
                    out.coverage.append(adapters.coverage("Mondo", [mondo_id], utc_now(), returned=0, inspected=1,
                                                          completion="partial", failure_reason=str(exc)))
                    continue
                out.entities.append(entity)
                out.mappings += mappings
        except FetchError as exc:
            _failed(out, "Mondo", mondo_cfg.get("obo_url", ""), exc)
    elif diseases:
        out.coverage.append(adapters.coverage("Mondo", diseases, utc_now(), returned=0, inspected=0, completion="failed",
                                              failure_reason="no pinned Mondo release/URLs configured"))

    for symbol in targets.get("genes", []):
        url = HGNC_FETCH + symbol
        try:
            cap = fetcher.get(url)
            out.extend(adapters.hgnc_gene(cap, cap.body(ws.cache), symbol))
        except FetchError as exc:
            _failed(out, "HGNC", url, exc)

    pubmed = (targets.get("pubmed") or {}).get("pmids", {})
    if pubmed:
        params = {"db": "pubmed", "id": ",".join(sorted(pubmed)), "retmode": "xml", "tool": "atlas-pipeline"}
        if os.environ.get("NCBI_CONTACT_EMAIL"):
            params["email"] = os.environ["NCBI_CONTACT_EMAIL"]
        if os.environ.get("NCBI_API_KEY"):
            params["api_key"] = os.environ["NCBI_API_KEY"]
        try:
            cap = fetcher.get(EUTILS_EFETCH, params=params)
            policies = {pmid: cfg.get("public_text_policy", "link_only") for pmid, cfg in pubmed.items()}
            out.extend(adapters.pubmed_articles(cap, cap.body(ws.cache), policies))
        except FetchError as exc:
            _failed(out, "PubMed EFetch", EUTILS_EFETCH, exc)

    for item in targets.get("medrxiv", []):
        url = f"{MEDRXIV_DETAILS}{item['doi']}/na/json"
        try:
            cap = fetcher.get(url)
            out.extend(adapters.medrxiv_preprint(
                cap, cap.body(ws.cache), item["doi"], str(item["version"]),
                public_text_policy=item.get("public_text_policy", "link_only"),
                publication_family_id=item.get("publication_family_id"), document_role=item["document_role"]))
        except FetchError as exc:
            _failed(out, "medRxiv details", url, exc)

    for item in targets.get("clinicaltrials", []):
        url = CTGOV_STUDY + item["nct"]
        try:
            cap = fetcher.get(url)
            out.extend(adapters.clinical_trial(cap, cap.body(ws.cache), item.get("condition_disease_ids", {})))
        except FetchError as exc:
            _failed(out, "ClinicalTrials.gov", url, exc)

    pages = json.loads((ws.config / "approved_urls.json").read_text()) if (ws.config / "approved_urls.json").exists() else {}
    for page in pages.get("pages", []):
        try:
            cap = fetcher.get(page["url"])
            out.documents.append(web_page(cap, cap.body(ws.cache), title=page["title"],
                                          source_kind=page["source_kind"],
                                          public_text_policy=page["public_text_policy"]))
            out.coverage.append(adapters.coverage("Approved web pages", [page["url"]], cap.fetched_at, returned=1,
                                                  inspected=1, completion="complete_for_query"))
        except (FetchError, ValueError) as exc:
            reason = exc.reason if isinstance(exc, FetchError) else str(exc)
            out.coverage.append(adapters.coverage("Approved web pages", [page["url"]], utc_now(), returned=0,
                                                  inspected=0, completion="failed", failure_reason=reason))

    ws.write_part("sources", out)
    return out


def run_backbone(ws: Workspace, targets: dict[str, Any]) -> AdapterOutput:
    """Structured candidate assertions plus manual imports, from cached stage-1 outputs only."""
    out = AdapterOutput()
    catalog = ws.catalog()
    ontology, _, _ = load_reference(ws)
    pointer = json.loads((ws.stage / "hpo_reference.json").read_text()) if (ws.stage / "hpo_reference.json").exists() else None
    sssom_docs = [d for d in catalog.sources.values() if d.title == "Mondo mappings (SSSOM)"]
    if pointer and pointer.get("hpoa_raw_sha256") and sssom_docs:
        hpoa_raw = (ws.cache / "raw" / pointer["hpoa_raw_sha256"]).read_text(encoding="utf-8")
        annotations = parse_hpoa(hpoa_raw, pointer["hpoa_raw_sha256"])
        hpoa_doc = catalog.sources[pointer["hpoa_document_id"]]
        _, rows = parse_sssom(sssom_docs[0].canonical_text)
        phenotypes: dict[str, Entity] = {}
        for mondo_id in targets.get("diseases", []):
            if mondo_id in catalog.entities:
                out.extend(adapters.disease_baseline_rows(mondo_id, rows, annotations, ontology, hpoa_doc, phenotypes,
                                                          pointer["obo_document_id"]))
        out.entities += sorted(phenotypes.values(), key=lambda e: e.id)
    contexts = {c.id: c for c in read_jsonl(ws.imports / "contexts.jsonl", ResearchContext)}
    documents = dict(catalog.sources)
    out.extend(normalize_imports(
        organizations=read_jsonl(ws.imports / "communities.jsonl", OrganizationImport),
        assets=read_jsonl(ws.imports / "assets.jsonl", AssetImport),
        studies=read_jsonl(ws.imports / "studies.jsonl", StudyImport),
        people=read_jsonl(ws.imports / "people.jsonl", PersonImport),
        documents=documents, contexts=contexts))
    ws.write_part("backbone", out)
    return out


def phenotype_entities_for(ws: Workspace, term_ids: set[str]) -> list[Entity]:
    """Entities for literature-linked phenotype terms from the full pinned ontology."""
    ontology, _, ids = load_reference(ws)
    doc_id = ids[0] if ids else None
    return [adapters.phenotype_entity(t, ontology, doc_id) for t in sorted(term_ids)
            if t in ontology.document.terms and doc_id]


def write_jsonl_file(path: Path, records) -> None:
    write_jsonl(path, records)


class OntologyBackedEntities(dict):
    """Catalog entities plus on-demand phenotype entities from the full pinned HPO ontology."""

    def __init__(self, entities: dict[str, Entity], ws: Workspace) -> None:
        super().__init__(entities)
        self._ws = ws

    def get(self, key, default=None):
        if key in self:
            return self[key]
        if isinstance(key, str) and key.startswith("HP:"):
            found = phenotype_entities_for(self._ws, {key})
            if found:
                self[key] = found[0]
                return found[0]
        return default
