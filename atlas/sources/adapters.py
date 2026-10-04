"""Source-to-record adapters. Each maps one pinned source format to records.

Adapters preserve native relations, classifications and qualifiers. They never
upgrade a relation (GO involvement is not disruption, trial registration is
not efficacy, a mention is not ownership) and never infer a mechanism. Output
assertions are `pending` until a human review is imported.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from typing import Any

from atlas.hashing import canonical_json, sha256_bytes, stable_id
from atlas.models.records import Alias, CoverageRecord, Entity, Mapping, Scope, Section
from atlas.sources.builders import AdapterOutput, claim, make_document, source_metadata, structured_evidence
from atlas.sources.canonical import TEXT_CANONICALIZER, canonicalize_text
from atlas.sources.fetch import Capture
from atlas.sources.hpo import HpoaFile, HpoOntology
from atlas.sources.mondo import SssomRow, exact_annotation_ids
from atlas.sources.obo import OboDocument

JSON_CANONICALIZER = "json-canonical-1"
PUBMED_CANONICALIZER = "pubmed-efetch-xml-1"
MEDRXIV_CANONICALIZER = "medrxiv-details-json-1"
TSV_ROW_CANONICALIZER = "tsv-text-1"
MISSING_SENTINELS = {"NA", "N/A", "", "null", "None"}


def _missing_to_none(value: Any) -> Any:
    return None if isinstance(value, str) and value.strip() in MISSING_SENTINELS else value


def coverage(source: str, queries: list[str], searched_at: str, *, returned: int, inspected: int,
             completion: str, failure_reason: str | None = None, result_cap: int | None = None,
             filters: dict[str, str] | None = None) -> CoverageRecord:
    return CoverageRecord(
        id=stable_id("cov", [source, sorted(queries), filters or {}, result_cap]), source=source,
        query_or_urls=sorted(queries), searched_at=searched_at, filters=filters or {}, result_cap=result_cap,
        returned_count=returned, inspected_count=inspected, completion=completion, failure_reason=failure_reason,
    )


# ---------------------------------------------------------------- HGNC


def hgnc_gene(capture: Capture, body: bytes, symbol: str) -> AdapterOutput:
    """HGNC REST `fetch/symbol` JSON. Exact symbol only; aliases kept typed."""
    out = AdapterOutput()
    data = json.loads(body)
    docs = [d for d in data.get("response", {}).get("docs", []) if d.get("symbol") == symbol]
    if len(docs) != 1:
        out.coverage.append(coverage("HGNC", [capture.target_url], capture.fetched_at, returned=len(docs),
                                     inspected=len(docs), completion="partial",
                                     failure_reason=f"expected exactly one approved symbol {symbol}, found {len(docs)}"))
        return out
    record = docs[0]
    text = canonical_json(record)
    document = make_document(
        source_kind="gene_registry", external_ref=record["hgnc_id"], title=f"HGNC record {record['hgnc_id']} ({symbol})",
        url=capture.target_url, release_or_version=record.get("date_modified"), published_at=None,
        fetched_at=capture.fetched_at, raw_sha256=capture.raw_sha256, canonical_text=text,
        canonicalizer_version=JSON_CANONICALIZER, sections=[], public_text_policy="full", origin="api",
        metadata=source_metadata(fetch_backend=capture.backend, original_target_url=capture.target_url),
    )
    aliases = [Alias(text=symbol, alias_type="symbol", source_document_id=document.id)]
    aliases += [Alias(text=a, alias_type="alias_symbol", source_document_id=document.id) for a in record.get("alias_symbol", [])]
    aliases += [Alias(text=a, alias_type="previous_symbol", source_document_id=document.id) for a in record.get("prev_symbol", [])]
    if record.get("name"):
        aliases.append(Alias(text=record["name"], alias_type="exact_synonym", source_document_id=document.id))
    out.documents.append(document)
    out.entities.append(Entity(
        id=record["hgnc_id"], type="gene", label=symbol, aliases=aliases, external_ids=[record["hgnc_id"]],
        properties={"symbol": symbol, "hgnc_id": record["hgnc_id"], "gene_family": None},
        identity_source_ids=[document.id],
    ))
    out.coverage.append(coverage("HGNC", [capture.target_url], capture.fetched_at, returned=1, inspected=1,
                                 completion="complete_for_query"))
    return out


# ---------------------------------------------------------------- Mondo / HPO identities


def mondo_disease(mondo_id: str, mondo: OboDocument, mondo_doc_id: str, sssom_rows: list[SssomRow],
                  sssom_doc_id: str) -> tuple[Entity, list[Mapping]]:
    term = mondo.terms.get(mondo_id)
    if term is None or term.name is None:
        raise KeyError(f"{mondo_id} is not in the pinned Mondo release")
    aliases = [Alias(text=term.name, alias_type="label", source_document_id=mondo_doc_id)]
    scope_map = {"EXACT": "exact_synonym", "BROAD": "broad_synonym", "NARROW": "narrow_synonym", "RELATED": "related_synonym"}
    aliases += [Alias(text=t, alias_type=scope_map[s], source_document_id=mondo_doc_id) for t, s in term.synonyms]
    mappings = [
        Mapping(id=Mapping.make_id(mondo_id, r.object_id, r.relation, sssom_doc_id), source_id=mondo_id,
                target_id=r.object_id, relation=r.relation, source_document_id=sssom_doc_id, review_state="pending")
        for r in sssom_rows if r.subject_id == mondo_id and r.relation is not None
    ]
    exact = [r.object_id for r in exact_annotation_ids(mondo_id, sssom_rows)]
    entity = Entity(id=mondo_id, type="disease", label=term.name, aliases=aliases, external_ids=exact,
                    properties={"mondo_id": mondo_id, "definition": None},
                    identity_source_ids=sorted({mondo_doc_id, sssom_doc_id}))
    return entity, sorted(mappings, key=lambda m: m.id)


def phenotype_entity(term_id: str, ontology: HpoOntology, hpo_doc_id: str) -> Entity:
    """Full typed alias set for one term of the pinned HPO release."""
    term = ontology.document.terms[term_id]
    scope_map = {"EXACT": "exact_synonym", "BROAD": "broad_synonym", "NARROW": "narrow_synonym", "RELATED": "related_synonym"}
    aliases = [Alias(text=term.name or term_id, alias_type="label", source_document_id=hpo_doc_id)]
    aliases += [Alias(text=t, alias_type=scope_map[s], source_document_id=hpo_doc_id) for t, s in term.synonyms]
    aliases += [Alias(text=a, alias_type="alt_id", source_document_id=hpo_doc_id) for a in term.alt_ids]
    replaced = term.replaced_by[0] if len(term.replaced_by) == 1 else None
    return Entity(id=term_id, type="phenotype", label=term.name or term_id, aliases=aliases, external_ids=[term_id],
                  properties={"hpo_id": term_id, "is_obsolete": term.is_obsolete, "replaced_by": replaced},
                  identity_source_ids=[hpo_doc_id])


def hpoa_document(annotations: HpoaFile, raw_text: str, capture: Capture) -> "Any":
    """The annotation file as one structured source; rows are addressed by line number."""
    text = canonicalize_text(raw_text)
    return make_document(
        source_kind="curation_database", external_ref="HPO:phenotype.hpoa", title="HPO phenotype.hpoa annotations",
        url=capture.target_url, release_or_version=annotations.release, published_at=None,
        fetched_at=capture.fetched_at, raw_sha256=capture.raw_sha256, canonical_text=text,
        canonicalizer_version=TSV_ROW_CANONICALIZER, sections=[], public_text_policy="excerpt", origin="download",
        metadata=source_metadata(fetch_backend=capture.backend, original_target_url=capture.target_url,
                                 license="HPO annotation terms of use; see source"),
    )


def disease_baseline_rows(mondo_id: str, sssom_rows: list[SssomRow], annotations: HpoaFile,
                          ontology: HpoOntology, hpoa_doc, phenotype_entities: dict[str, Entity],
                          ontology_document_id: str) -> AdapterOutput:
    """Mapped HPO disease annotations for one Mondo disease (exact mappings only).

    Rows from several exact-mapped IDs are unioned with original row provenance;
    NOT rows become `negated` assertions (negative evidence). Every row keeps
    frequency, onset, reference and evidence code. An exact mapping without
    annotation rows contributes nothing; no mapping means no baseline.
    """
    out = AdapterOutput()
    mapped = {r.object_id for r in exact_annotation_ids(mondo_id, sssom_rows)}
    for row in annotations.rows:
        if row.database_id not in mapped or row.aspect != "P":
            continue
        term = ontology.resolve(row.hpo_id)
        if term is None:
            out.coverage.append(coverage("HPO annotations", [f"{row.database_id}:{row.hpo_id}"], hpoa_doc.fetched_at,
                                         returned=1, inspected=1, completion="partial",
                                         failure_reason=f"line {row.line_number}: {row.hpo_id} has no unambiguous current term"))
            continue
        if term not in phenotype_entities:
            phenotype_entities[term] = phenotype_entity(term, ontology, ontology_document_id)
        qualifiers = [f"HPO frequency: {row.frequency}"] if row.frequency else []
        qualifiers += [f"HPO onset: {row.onset}"] if row.onset else []
        qualifiers += [f"HPO sex: {row.sex}"] if row.sex else []
        qualifiers += [f"HPO modifier: {row.modifier}"] if row.modifier else []
        evidence = structured_evidence(
            hpoa_doc, f"phenotype.hpoa:line:{row.line_number}", row.fields(), study_design=f"HPO evidence code {row.evidence}",
            species="human", extra_metadata={"reference": row.reference, "mapped_annotation_id": row.database_id},
        )
        claim(out, subject_id=mondo_id, predicate="has_phenotype", object_id=term, context_id=None,
              scope=Scope(**{**Scope.empty().payload(), "other_qualifiers": sorted(qualifiers)}),
              effect_direction="not_applicable", statement_status="negated" if row.negated else "listed_record",
              origin="source_adapter", evidence=evidence)
    return out


# ---------------------------------------------------------------- PubMed


def pubmed_articles(capture: Capture, body: bytes, policies: dict[str, str]) -> AdapterOutput:
    """EFetch PubMed XML for selected PMIDs; canonical text = title + labelled abstract sections."""
    out = AdapterOutput()
    root = ET.fromstring(body)
    found: list[str] = []
    for article in root.iter("PubmedArticle"):
        pmid = (article.findtext("MedlineCitation/PMID") or "").strip()
        art = article.find("MedlineCitation/Article")
        if not pmid or art is None:
            continue
        found.append(pmid)
        title = "".join(art.find("ArticleTitle").itertext()).strip() if art.find("ArticleTitle") is not None else f"PMID {pmid}"
        parts: list[tuple[str, str]] = [("title", title)]
        for index, node in enumerate(art.findall("Abstract/AbstractText"), start=1):
            label = node.get("Label") or ("abstract" if len(art.findall("Abstract/AbstractText")) == 1 else f"abstract part {index}")
            parts.append((label, "".join(node.itertext()).strip()))
        text_chunks, sections, cursor = [], [], 0
        for label, chunk in parts:
            chunk = canonicalize_text(chunk)
            if text_chunks:
                text_chunks.append("\n\n")
                cursor += 2
            sections.append(Section(label=label, start=cursor, end=cursor + len(chunk)))
            text_chunks.append(chunk)
            cursor += len(chunk)
        doi = next((i.text for i in article.findall("PubmedData/ArticleIdList/ArticleId") if i.get("IdType") == "doi"), None)
        year = article.findtext("MedlineCitation/Article/Journal/JournalIssue/PubDate/Year")
        out.documents.append(make_document(
            source_kind="literature", external_ref=f"PMID:{pmid}", title=title,
            url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/", release_or_version=None, published_at=year,
            fetched_at=capture.fetched_at, raw_sha256=capture.raw_sha256, canonical_text="".join(text_chunks),
            canonicalizer_version=PUBMED_CANONICALIZER, sections=sections,
            public_text_policy=policies.get(pmid, "link_only"), origin="api",
            metadata=source_metadata(doi=doi, publication_family_id=f"family:pmid:{pmid}", fetch_backend=capture.backend,
                                     original_target_url=capture.target_url, publication_status="published",
                                     journal=article.findtext("MedlineCitation/Article/Journal/Title")),
        ))
    requested = sorted(policies)
    missing = sorted(set(requested) - set(found))
    out.coverage.append(coverage("PubMed EFetch", [f"PMID:{p}" for p in requested], capture.fetched_at,
                                 returned=len(found), inspected=len(found),
                                 completion="partial" if missing else "complete_for_query",
                                 failure_reason=f"not returned: {', '.join(missing)}" if missing else None))
    return out


# ---------------------------------------------------------------- medRxiv


def medrxiv_preprint(capture: Capture, body: bytes, doi: str, pinned_version: str, *, public_text_policy: str,
                     publication_family_id: str | None, document_role: str) -> AdapterOutput:
    """Selected-DOI `details` response, pinned to one manuscript version.

    `published` sentinels (e.g. the literal "NA") normalize to null while the
    original field values are preserved. A journal link does not change this
    source's own `preprint` status; versions and the article form one
    publication family. `document_role` (e.g. protocol) is set by a human in
    the source manifest, never inferred from wording.
    """
    out = AdapterOutput()
    data = json.loads(body)
    rows = [r for r in data.get("collection", []) if r.get("doi") == doi]
    chosen = [r for r in rows if str(r.get("version")) == str(pinned_version)]
    if len(chosen) != 1:
        out.coverage.append(coverage("medRxiv details", [capture.target_url], capture.fetched_at, returned=len(rows),
                                     inspected=len(rows), completion="partial",
                                     failure_reason=f"pinned version {pinned_version} not uniquely present"))
        return out
    row = chosen[0]
    title = canonicalize_text(str(row.get("title", "")).strip())
    abstract = canonicalize_text(str(row.get("abstract", "")).strip())
    text = f"{title}\n\n{abstract}"
    sections = [Section(label="title", start=0, end=len(title)),
                Section(label="abstract", start=len(title) + 2, end=len(text))]
    published = _missing_to_none(row.get("published"))
    original_fields = {k: v for k, v in row.items() if k != "abstract"}
    out.documents.append(make_document(
        source_kind="literature", external_ref=f"DOI:{doi}", title=title or f"medRxiv {doi}",
        url=f"https://www.medrxiv.org/content/{doi}v{pinned_version}", release_or_version=str(pinned_version),
        published_at=_missing_to_none(row.get("date")), fetched_at=capture.fetched_at, raw_sha256=capture.raw_sha256,
        canonical_text=text, canonicalizer_version=MEDRXIV_CANONICALIZER, sections=sections,
        public_text_policy=public_text_policy, origin="api",
        metadata=source_metadata(
            server=_missing_to_none(row.get("server")) or "medrxiv", doi=doi, manuscript_version=str(pinned_version),
            license=_missing_to_none(row.get("license")), published_doi=published,
            publication_family_id=publication_family_id or f"family:doi:{doi}", fetch_backend=capture.backend,
            original_target_url=capture.target_url, publication_status="preprint", document_role=document_role,
            original_fields=original_fields,
        ),
    ))
    out.coverage.append(coverage("medRxiv details", [capture.target_url], capture.fetched_at, returned=len(rows),
                                 inspected=1, completion="complete_for_query"))
    return out


# ---------------------------------------------------------------- ClinicalTrials.gov


def clinical_trial(capture: Capture, body: bytes, condition_disease_ids: dict[str, str]) -> AdapterOutput:
    """ClinicalTrials.gov API v2 single-study JSON: registered design/status, never efficacy.

    `studies` assertions are created only for conditions that a human mapped to
    catalog diseases in config; free-text conditions are never auto-linked.
    """
    out = AdapterOutput()
    data = json.loads(body)
    protocol = data.get("protocolSection", {})
    ident = protocol.get("identificationModule", {})
    status = protocol.get("statusModule", {})
    design = protocol.get("designModule", {})
    conditions = protocol.get("conditionsModule", {}).get("conditions", [])
    nct = ident.get("nctId")
    if not nct:
        raise ValueError("ClinicalTrials.gov record lacks protocolSection.identificationModule.nctId")
    document = make_document(
        source_kind="trial_registry", external_ref=nct, title=ident.get("briefTitle") or nct,
        url=f"https://clinicaltrials.gov/study/{nct}", release_or_version=status.get("lastUpdatePostDateStruct", {}).get("date"),
        published_at=None, fetched_at=capture.fetched_at, raw_sha256=capture.raw_sha256,
        canonical_text=canonical_json(data), canonicalizer_version=JSON_CANONICALIZER, sections=[],
        public_text_policy="full", origin="api",
        metadata=source_metadata(fetch_backend=capture.backend, original_target_url=capture.target_url),
    )
    out.documents.append(document)
    study_design = design.get("studyType") or "not stated"
    out.entities.append(Entity(
        id=nct, type="study", label=ident.get("briefTitle") or nct,
        aliases=[Alias(text=nct, alias_type="label", source_document_id=document.id)], external_ids=[nct],
        properties={"nct_id": nct, "design": study_design, "status_as_reported": status.get("overallStatus") or "not stated",
                    "status_date": status.get("statusVerifiedDate"), "conditions": conditions,
                    "eligibility_reference": f"https://clinicaltrials.gov/study/{nct}#participation-criteria"},
        identity_source_ids=[document.id],
    ))
    for condition in conditions:
        disease_id = condition_disease_ids.get(condition)
        if disease_id is None:
            continue
        evidence = structured_evidence(
            document, f"{nct}#/protocolSection/conditionsModule/conditions",
            [("nct_id", nct), ("condition", condition), ("overall_status", status.get("overallStatus") or ""),
             ("study_type", study_design)], study_design=study_design)
        claim(out, subject_id=nct, predicate="studies", object_id=disease_id, context_id=None, scope=Scope.empty(),
              effect_direction="not_applicable", statement_status="listed_record", origin="source_adapter",
              evidence=evidence)
    return out


# ---------------------------------------------------------------- GenCC / GO


# Source vocabulary values that must never become positive established evidence.
NON_POSITIVE_NATIVE_VALIDITY = frozenset({
    "Refuted Evidence", "Disputed Evidence", "Limited", "No Known Disease Relationship", "Animal Model Only",
})


def gencc_rows(document, rows: list[dict[str, str]], gene_ids: dict[str, str], disease_ids: dict[str, str]) -> AdapterOutput:
    """GenCC submission rows: `classification_title` is kept verbatim as native validity."""
    out = AdapterOutput()
    for index, row in enumerate(rows, start=1):
        gene, disease = gene_ids.get(row["gene_curie"]), disease_ids.get(row["disease_curie"])
        if gene is None or disease is None:
            continue
        evidence = structured_evidence(
            document, f"gencc:row:{index}", sorted(row.items()), study_design="curated gene-disease validity",
            source_native_validity=row["classification_title"])
        claim(out, subject_id=gene, predicate="gene_associated_with_disease", object_id=disease, context_id=None,
              scope=Scope(**{**Scope.empty().payload(), "other_qualifiers": [f"GenCC submitter: {row.get('submitter_title', '')}",
                                                                              f"Mode of inheritance: {row.get('moi_title', '')}"]}),
              effect_direction="not_applicable", statement_status="listed_record", origin="source_adapter",
              evidence=evidence)
    return out


def go_annotations(document, gaf_lines: list[str], gene_by_symbol: dict[str, str], process_ids: set[str]) -> AdapterOutput:
    """GAF 2.x biological-process rows -> `gene_involved_in_process` with the GO qualifier preserved.

    Involvement or normal function never becomes disease disruption. NOT rows
    are negated assertions.
    """
    out = AdapterOutput()
    for number, line in enumerate(gaf_lines, start=1):
        if line.startswith("!") or not line.strip():
            continue
        cols = line.split("\t")
        if len(cols) < 15 or cols[8] != "P":
            continue
        symbol, qualifier, go_id = cols[2], cols[3], cols[4]
        gene = gene_by_symbol.get(symbol)
        if gene is None or go_id not in process_ids:
            continue
        negated = "NOT" in qualifier.split("|")
        evidence = structured_evidence(
            document, f"gaf:line:{number}", [("db_object_symbol", symbol), ("qualifier", qualifier), ("go_id", go_id),
                                             ("reference", cols[5]), ("evidence_code", cols[6])],
            study_design=f"GO evidence code {cols[6]}")
        claim(out, subject_id=gene, predicate="gene_involved_in_process", object_id=go_id, context_id=None,
              scope=Scope(**{**Scope.empty().payload(), "other_qualifiers": [f"GO qualifier: {qualifier}"]}),
              effect_direction="not_applicable", statement_status="negated" if negated else "listed_record",
              origin="source_adapter", evidence=evidence)
    return out


def raw_digest(body: bytes) -> str:
    return sha256_bytes(body)
