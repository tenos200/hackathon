"""Loaders for the team's uploaded bulk captures (see data manifest).

Formats: HPO OBO Graphs JSON, GenCC submissions CSV, GO GAF 2.2 (gzip),
Orphadata product6 XML (gene-disorder associations), ClinicalTrials.gov v2
search pages, NIH RePORTER project search responses, PubMed EFetch batches and
medRxiv `details` responses.

Large reference files are identified by their raw SHA-256 through a short
descriptor text (canonicalizer `bulk-descriptor-1`) instead of being copied
inline; records inside them are cited by exact locators and their original
field values. Nothing here infers meaning from prose: controlled-vocabulary
fields are copied verbatim, entity links use exact identifiers or exact
approved symbols/identity aliases only, and every assertion starts pending.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Iterable

from atlas.hashing import stable_id
from atlas.models.records import Alias, Entity, Scope, Section, SourceDocument, Span
from atlas.sources.builders import AdapterOutput, claim, make_document, source_metadata, structured_evidence, text_evidence_from_spans
from atlas.sources.canonical import canonicalize_text
from atlas.sources.fetch import Capture
from atlas.sources.mondo import SssomRow
from atlas.sources.obo import OboDocument, OboTerm

BULK_CANONICALIZER = "bulk-descriptor-1"
PROJECT_CANONICALIZER = "reporter-project-text-1"
OBO_PREFIX = "http://purl.obolibrary.org/obo/"
SYNONYM_SCOPES = {"hasExactSynonym": "EXACT", "hasBroadSynonym": "BROAD", "hasNarrowSynonym": "NARROW",
                  "hasRelatedSynonym": "RELATED"}
ALT_ID = "http://www.geneontology.org/formats/oboInOwl#hasAlternativeId"
REPLACED_BY = "http://purl.obolibrary.org/obo/IAO_0100001"


def bulk_document(capture: Capture, *, title: str, source_kind: str, release: str | None, external_ref: str,
                  public_text_policy: str = "link_only", **extra: Any) -> SourceDocument:
    """A large file identified by its raw hash; its records are cited by locator."""
    text = (f"{title}\nsource: {capture.target_url}\nrelease: {release or 'not stated'}\n"
            f"raw_sha256: {capture.raw_sha256}\n")
    return make_document(
        source_kind=source_kind, external_ref=external_ref, title=title, url=capture.target_url,
        release_or_version=release, published_at=None, fetched_at=capture.fetched_at, raw_sha256=capture.raw_sha256,
        canonical_text=text, canonicalizer_version=BULK_CANONICALIZER, sections=[],
        public_text_policy=public_text_policy, origin="download",
        metadata=source_metadata(fetch_backend=capture.backend, original_target_url=capture.target_url,
                                 publication_status="unknown", **extra))


# ---------------------------------------------------------------- HPO OBO Graphs JSON


def _curie(iri: str) -> str:
    if iri.startswith(OBO_PREFIX):
        local = iri[len(OBO_PREFIX):]
        prefix, _, number = local.partition("_")
        return f"{prefix}:{number}" if number else local
    return iri


def parse_obographs(data: bytes | str) -> OboDocument:
    graph = json.loads(data)["graphs"][0]
    version = (graph.get("meta") or {}).get("version")
    release = None
    if version:
        match = re.search(r"releases/([^/]+)/", version)
        release = f"hp/releases/{match.group(1)}" if match else version
    terms: dict[str, OboTerm] = {}
    for node in graph["nodes"]:
        if node.get("type") != "CLASS":
            continue
        tid = _curie(node["id"])
        if not tid.startswith("HP:"):
            continue
        meta = node.get("meta") or {}
        term = OboTerm(id=tid, name=node.get("lbl"), is_obsolete=bool(meta.get("deprecated")))
        for syn in meta.get("synonyms", []):
            scope = SYNONYM_SCOPES.get(syn.get("pred"))
            if scope and syn.get("val"):
                term.synonyms.append((syn["val"], scope))
        for prop in meta.get("basicPropertyValues", []):
            if prop.get("pred") == ALT_ID:
                term.alt_ids.append(prop["val"])
            elif prop.get("pred") == REPLACED_BY:
                term.replaced_by.append(_curie(prop["val"]) if prop["val"].startswith("http") else prop["val"])
        terms[tid] = term
    for edge in graph.get("edges", []):
        if edge.get("pred") == "is_a":
            sub, obj = _curie(edge["sub"]), _curie(edge["obj"])
            if sub in terms and obj.startswith("HP:"):
                terms[sub].is_a.append(obj)
    return OboDocument(data_version=release, terms=terms)


# ---------------------------------------------------------------- GenCC


def gencc_rows_for(text: str, gene_symbols: set[str]) -> list[tuple[int, dict[str, str]]]:
    """(1-based data row number, row) for the selected approved symbols."""
    reader = csv.DictReader(io.StringIO(text))
    return [(n, row) for n, row in enumerate(reader, start=1) if row.get("gene_symbol") in gene_symbols]


GENCC_FIELDS = ("sgc_id", "gene_curie", "gene_symbol", "disease_curie", "disease_title", "disease_original_curie",
                "classification_title", "moi_title", "submitter_title", "submitted_as_date", "submitted_as_pmids",
                "submitted_mondo_curie")


def gencc_assertions(document: SourceDocument, rows: list[tuple[int, dict[str, str]]], genes: dict[str, str],
                     diseases: set[str]) -> AdapterOutput:
    """`gene_associated_with_disease` per submission, classification kept verbatim as native validity."""
    out = AdapterOutput()
    for number, row in rows:
        gene = genes.get(row["gene_curie"])
        if gene is None or row["disease_curie"] not in diseases:
            continue
        fields = [(k, row.get(k, "")) for k in GENCC_FIELDS]
        evidence = structured_evidence(document, f"gencc-submissions.csv:row:{number}", fields,
                                       study_design="curated gene-disease validity submission",
                                       source_native_validity=row["classification_title"],
                                       extra_metadata={"reference": row["sgc_id"]})
        qualifiers = [f"GenCC submitter: {row['submitter_title']}"] if row.get("submitter_title") else []
        qualifiers += [f"Mode of inheritance: {row['moi_title']}"] if row.get("moi_title") else []
        claim(out, subject_id=gene, predicate="gene_associated_with_disease", object_id=row["disease_curie"],
              context_id=None, scope=Scope(**{**Scope.empty().payload(), "other_qualifiers": sorted(qualifiers)}),
              effect_direction="not_applicable", statement_status="listed_record", origin="source_adapter",
              evidence=evidence)
    return out


# ---------------------------------------------------------------- GO GAF


def gaf_rows_for(gz_bytes: bytes, symbols: set[str]) -> list[tuple[int, list[str]]]:
    rows = []
    with gzip.open(io.BytesIO(gz_bytes), "rt", encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if line.startswith("!"):
                continue
            cols = line.rstrip("\n").split("\t")
            if len(cols) >= 15 and cols[2] in symbols and cols[8] == "P":
                rows.append((number, cols))
    return rows


def go_assertions(document: SourceDocument, rows: list[tuple[int, list[str]]], genes_by_symbol: dict[str, str],
                  go_labels: dict[str, str]) -> AdapterOutput:
    """`gene_involved_in_process` with the GO qualifier preserved; involvement is never disruption."""
    out = AdapterOutput()
    processes: dict[str, Entity] = {}
    for number, cols in rows:
        symbol, qualifier, go_id, reference, code = cols[2], cols[3], cols[4], cols[5], cols[6]
        gene = genes_by_symbol.get(symbol)
        if gene is None:
            continue
        if go_id not in processes:
            label = go_labels.get(go_id)
            processes[go_id] = Entity(
                id=go_id, type="process", label=label or go_id,
                aliases=[Alias(text=go_id, alias_type="label", source_document_id=document.id)], external_ids=[go_id],
                properties={"go_id": go_id}, identity_source_ids=[document.id])
        evidence = structured_evidence(
            document, f"goa_human.gaf:line:{number}",
            [("db_object_symbol", symbol), ("qualifier", qualifier), ("go_id", go_id), ("reference", reference),
             ("evidence_code", code), ("assigned_by", cols[14])], study_design=f"GO evidence code {code}",
            extra_metadata={"reference": reference})
        claim(out, subject_id=gene, predicate="gene_involved_in_process", object_id=go_id, context_id=None,
              scope=Scope(**{**Scope.empty().payload(), "other_qualifiers": [f"GO qualifier: {qualifier}"]}),
              effect_direction="not_applicable",
              statement_status="negated" if "NOT" in qualifier.split("|") else "listed_record",
              origin="source_adapter", evidence=evidence)
    out.entities += sorted(processes.values(), key=lambda e: e.id)
    return out


# ---------------------------------------------------------------- Orphadata product6


@dataclass(frozen=True)
class OrphaAssociation:
    orpha_code: str
    disorder_name: str
    gene_symbol: str
    gene_hgnc: str | None
    association_type: str
    association_status: str
    source_of_validation: str


def orphanet_associations(xml_bytes: bytes, symbols: set[str]) -> tuple[str | None, list[OrphaAssociation]]:
    root = ET.fromstring(xml_bytes)
    release = root.get("date")
    found = []
    for disorder in root.iter("Disorder"):
        code = disorder.findtext("OrphaCode")
        name = disorder.findtext("Name") or ""
        for assoc in disorder.iter("DisorderGeneAssociation"):
            symbol = assoc.findtext("Gene/Symbol")
            if symbol not in symbols:
                continue
            hgnc = next((r.findtext("Reference") for r in assoc.iter("ExternalReference")
                         if r.findtext("Source") == "HGNC"), None)
            found.append(OrphaAssociation(
                orpha_code=code, disorder_name=name, gene_symbol=symbol, gene_hgnc=f"HGNC:{hgnc}" if hgnc else None,
                association_type=assoc.findtext("DisorderGeneAssociationType/Name") or "",
                association_status=assoc.findtext("DisorderGeneAssociationStatus/Name") or "",
                source_of_validation=assoc.findtext("SourceOfValidation") or ""))
    return release, found


# Orphanet's controlled association-type vocabulary -> functional-effect category.
# Only these exact source terms state a functional effect; every other type is "not stated".
ORPHANET_EFFECT_TERMS = {
    "Disease-causing germline mutation(s) (loss of function) in": "loss",
    "Disease-causing germline mutation(s) (gain of function) in": "gain",
    "Disease-causing somatic mutation(s) (loss of function) in": "loss",
    "Disease-causing somatic mutation(s) (gain of function) in": "gain",
}


def orpha_to_mondo(rows: list[SssomRow]) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for r in rows:
        if r.relation == "exact" and r.object_id.startswith("ORPHA:") and r.subject_id.startswith("MONDO:"):
            index.setdefault(r.object_id, []).append(r.subject_id)
    return index


def orphanet_records(document: SourceDocument, associations: list[OrphaAssociation], genes: dict[str, str],
                     mondo_for_orpha: dict[str, list[str]]):
    """Gene-disorder associations as listed records; returns (output, mechanism proposals, unmapped)."""
    out = AdapterOutput()
    proposals: list[dict[str, Any]] = []
    unmapped: list[str] = []
    for a in associations:
        gene = genes.get(a.gene_hgnc or "") or genes.get(a.gene_symbol)
        mondos = mondo_for_orpha.get(f"ORPHA:{a.orpha_code}", [])
        if gene is None:
            continue
        if len(mondos) != 1:
            unmapped.append(f"ORPHA:{a.orpha_code} ({a.disorder_name}): {len(mondos)} exact Mondo mappings")
            continue
        fields = [("orpha_code", a.orpha_code), ("disorder_name", a.disorder_name), ("gene_symbol", a.gene_symbol),
                  ("association_type", a.association_type), ("association_status", a.association_status),
                  ("source_of_validation", a.source_of_validation)]
        locator = f"en_product6.xml#OrphaCode={a.orpha_code}/Gene={a.gene_symbol}"
        evidence = structured_evidence(document, locator, fields, study_design="Orphanet expert-curated association",
                                       source_native_validity=a.association_status,
                                       extra_metadata={"reference": f"ORPHA:{a.orpha_code}:{a.gene_symbol}"})
        claim(out, subject_id=gene, predicate="gene_associated_with_disease", object_id=mondos[0], context_id=None,
              scope=Scope(**{**Scope.empty().payload(),
                             "other_qualifiers": [f"Orphanet association type: {a.association_type}"]}),
              effect_direction="not_applicable", statement_status="listed_record", origin="source_adapter",
              evidence=evidence)
        effect = ORPHANET_EFFECT_TERMS.get(a.association_type)
        if effect and a.association_status == "Assessed":
            proposals.append({"gene_id": gene, "gene_symbol": a.gene_symbol, "disease_id": mondos[0],
                              "orpha_code": a.orpha_code, "disorder_name": a.disorder_name, "effect": effect,
                              "association_type": a.association_type, "evidence": evidence})
    return out, proposals, unmapped


# ---------------------------------------------------------------- ClinicalTrials.gov search pages


def ctgov_studies(body: bytes) -> list[dict[str, Any]]:
    return json.loads(body).get("studies", [])


# ---------------------------------------------------------------- NIH RePORTER


def reporter_projects(body: bytes) -> list[dict[str, Any]]:
    return json.loads(body).get("results", [])


def symbol_spans(text: str, symbol: str) -> list[tuple[int, int]]:
    """Exact approved-symbol occurrences on token boundaries (ordinary text retrieval)."""
    return [(m.start(), m.end()) for m in re.finditer(rf"(?<![A-Za-z0-9]){re.escape(symbol)}(?![A-Za-z0-9])", text)]


def reporter_records(capture: Capture, projects: Iterable[dict[str, Any]], genes_by_symbol: dict[str, str]) -> AdapterOutput:
    """Funded projects as study entities with sourced organization, funder, investigator and mention edges.

    Investigator identity uses the NIH RePORTER `profile_id` (a source-assigned
    identifier), never a name. `mentions` records only that the project text
    contains the approved gene symbol; it asserts no relationship.
    """
    out = AdapterOutput()
    seen_entities: dict[str, Entity] = {}

    def entity(e: Entity) -> None:
        seen_entities.setdefault(e.id, e)

    for project in projects:
        appl = str(project["appl_id"])
        title = canonicalize_text((project.get("project_title") or "").strip())
        abstract = canonicalize_text((project.get("abstract_text") or "").strip())
        text = f"{title}\n\n{abstract}" if abstract else title
        sections = [Section(label="title", start=0, end=len(title))]
        if abstract:
            sections.append(Section(label="abstract", start=len(title) + 2, end=len(text)))
        url = f"https://reporter.nih.gov/project-details/{appl}"
        document = make_document(
            source_kind="institutional_website", external_ref=f"NIH-RePORTER:{appl}", title=title or f"NIH project {appl}",
            url=url, release_or_version=str(project.get("fiscal_year")), published_at=None,
            fetched_at=capture.fetched_at, raw_sha256=capture.raw_sha256, canonical_text=text,
            canonicalizer_version=PROJECT_CANONICALIZER, sections=sections, public_text_policy="excerpt", origin="api",
            metadata=source_metadata(fetch_backend=capture.backend, original_target_url=capture.target_url,
                                     publication_status="unknown", license="NIH RePORTER public data"))
        if document.id not in {d.id for d in out.documents}:
            out.documents.append(document)
        study_id = f"study:nih-{project.get('project_num') or appl}"
        org = project.get("organization") or {}
        org_name = (org.get("org_name") or "").strip()
        org_id = stable_id("org", ["nih-reporter-org", org.get("external_org_id") or org_name])
        fields = [("appl_id", appl), ("project_num", str(project.get("project_num") or "")),
                  ("fiscal_year", str(project.get("fiscal_year") or "")),
                  ("organization", org_name), ("activity_code", str(project.get("activity_code") or "")),
                  ("award_amount", str(project.get("award_amount") or "")),
                  ("agency", ((project.get("agency_ic_admin") or {}).get("abbreviation") or "")),
                  ("project_start_date", str(project.get("project_start_date") or "")),
                  ("project_end_date", str(project.get("project_end_date") or ""))]
        record = structured_evidence(document, f"{url}#record", fields, study_design="NIH-funded research project record",
                                     extra_metadata={"reference": f"NIH-RePORTER:{appl}",
                                                     "ownership_relation": "applicant organization"})
        entity(Entity(id=study_id, type="study", label=title or study_id,
                      aliases=[Alias(text=str(project.get("project_num") or appl), alias_type="label",
                                     source_document_id=document.id)],
                      external_ids=[str(project.get("project_num") or appl)],
                      properties={"nct_id": None, "design": "NIH-funded research project",
                                  "status_as_reported": "active" if project.get("is_active") else "not active",
                                  "status_date": str(project.get("project_end_date") or "") or None, "conditions": [],
                                  "eligibility_reference": None},
                      identity_source_ids=[document.id]))
        if org_name:
            entity(Entity(id=org_id, type="organization", label=org_name.title(), aliases=[], external_ids=[],
                          properties={"official_url": None, "contact_url": None, "domain": None},
                          identity_source_ids=[document.id]))
            claim(out, subject_id=org_id, predicate="owns_or_runs", object_id=study_id, context_id=None,
                  scope=Scope.empty(), effect_direction="not_applicable", statement_status="listed_record",
                  origin="source_adapter", evidence=record)
        agency = project.get("agency_ic_admin") or {}
        if agency.get("abbreviation"):
            funder_id = f"org:nih-{agency['abbreviation'].lower()}"
            entity(Entity(id=funder_id, type="organization", label=agency.get("name") or agency["abbreviation"],
                          aliases=[Alias(text=agency["abbreviation"], alias_type="label", source_document_id=document.id)],
                          external_ids=[], properties={"official_url": None, "contact_url": None, "domain": None},
                          identity_source_ids=[document.id]))
            funding = structured_evidence(
                document, f"{url}#funding",
                [("agency", agency["abbreviation"]), ("agency_name", agency.get("name") or ""),
                 ("project_num", str(project.get("project_num") or "")),
                 ("fiscal_year", str(project.get("fiscal_year") or "")),
                 ("award_amount", str(project.get("award_amount") or ""))],
                study_design="NIH-funded research project record", extra_metadata={"reference": f"NIH-RePORTER:{appl}"})
            claim(out, subject_id=funder_id, predicate="funds", object_id=study_id, context_id=None,
                  scope=Scope.empty(), effect_direction="not_applicable", statement_status="listed_record",
                  origin="source_adapter", evidence=funding)
        for pi in project.get("principal_investigators") or []:
            if not pi.get("profile_id"):
                continue
            person_id = f"person:nih-reporter-{pi['profile_id']}"
            entity(Entity(id=person_id, type="person", label=(pi.get("full_name") or "").strip() or person_id,
                          aliases=[], external_ids=[f"NIH-RePORTER-profile:{pi['profile_id']}"],
                          properties={"profile_url": url, "orcid": None,
                                      "organization_id": org_id if pi.get("is_contact_pi") and org_name else None},
                          identity_source_ids=[document.id]))
            pi_record = structured_evidence(
                document, f"{url}#pi={pi['profile_id']}",
                [("profile_id", str(pi["profile_id"])), ("full_name", (pi.get("full_name") or "").strip()),
                 ("is_contact_pi", str(bool(pi.get("is_contact_pi")))), ("organization", org_name),
                 ("project_num", str(project.get("project_num") or ""))],
                study_design="NIH-funded research project record", extra_metadata={"reference": f"NIH-RePORTER:{appl}"})
            claim(out, subject_id=person_id, predicate="investigator_on", object_id=study_id, context_id=None,
                  scope=Scope.empty(), effect_direction="not_applicable", statement_status="listed_record",
                  origin="source_adapter", evidence=pi_record)
            if pi.get("is_contact_pi") and org_name:
                claim(out, subject_id=person_id, predicate="professional_at", object_id=org_id, context_id=None,
                      scope=Scope.empty(), effect_direction="not_applicable", statement_status="listed_record",
                      origin="source_adapter", evidence=pi_record)
        for symbol, gene in sorted(genes_by_symbol.items()):
            spans = symbol_spans(text, symbol)
            if not spans:
                continue
            first = spans[0]
            section = next((s.label for s in sections if s.start <= first[0] and first[1] <= s.end), None)
            evidence = text_evidence_from_spans(
                document, [Span(start=first[0], end=first[1], quote=symbol, section_label=section)],
                study_design="NIH-funded research project record", publication_status="unknown",
                extra_metadata={"reference": f"NIH-RePORTER:{appl}"})
            claim(out, subject_id=study_id, predicate="mentions", object_id=gene, context_id=None, scope=Scope.empty(),
                  effect_direction="not_applicable", statement_status="listed_record", origin="source_adapter",
                  evidence=evidence)
    out.entities += sorted(seen_entities.values(), key=lambda e: e.id)
    return out
