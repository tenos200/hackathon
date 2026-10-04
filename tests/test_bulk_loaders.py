"""Bulk loaders for the uploaded formats, on tiny synthetic inputs (offline-unit)."""

from __future__ import annotations

import gzip
import json

import pytest

from atlas.sources import bulk
from atlas.sources.fetch import Capture
from atlas.sources.hpo import HpoOntology
from atlas.sources.mondo import parse_sssom

CAP = Capture("https://example.invalid/x", "https://example.invalid/x", "direct", None, 200, {}, "2026-10-03T00:00:00Z",
              "rawsha", "raw/rawsha")


def test_obographs_hpo_keeps_synonym_scopes_alt_ids_obsolete_and_is_a():
    graph = {"graphs": [{"meta": {"version": "http://purl.obolibrary.org/obo/hp/releases/2026-09-01/hp.json"},
        "nodes": [
            {"id": "http://purl.obolibrary.org/obo/HP_0000001", "lbl": "All", "type": "CLASS"},
            {"id": "http://purl.obolibrary.org/obo/HP_0000118", "lbl": "Phenotypic abnormality", "type": "CLASS"},
            {"id": "http://purl.obolibrary.org/obo/HP_9000001", "lbl": "Synthetic seizure", "type": "CLASS", "meta": {
                "synonyms": [{"pred": "hasExactSynonym", "val": "Synthetic fit"},
                             {"pred": "hasRelatedSynonym", "val": "Synthetic spell"}],
                "basicPropertyValues": [{"pred": "http://www.geneontology.org/formats/oboInOwl#hasAlternativeId",
                                         "val": "HP:9000099"}]}},
            {"id": "http://purl.obolibrary.org/obo/HP_9000002", "lbl": "obsolete old", "type": "CLASS", "meta": {
                "deprecated": True,
                "basicPropertyValues": [{"pred": "http://purl.obolibrary.org/obo/IAO_0100001",
                                         "val": "http://purl.obolibrary.org/obo/HP_9000001"}]}},
            {"id": "http://purl.obolibrary.org/obo/UBERON_1", "lbl": "not HPO", "type": "CLASS"}],
        "edges": [{"sub": "http://purl.obolibrary.org/obo/HP_0000118", "pred": "is_a", "obj": "http://purl.obolibrary.org/obo/HP_0000001"},
                  {"sub": "http://purl.obolibrary.org/obo/HP_9000001", "pred": "is_a", "obj": "http://purl.obolibrary.org/obo/HP_0000118"}]}]}
    doc = bulk.parse_obographs(json.dumps(graph))
    assert doc.data_version == "hp/releases/2026-09-01" and "UBERON:1" not in doc.terms
    seizure = doc.terms["HP:9000001"]
    assert ("Synthetic fit", "EXACT") in seizure.synonyms and ("Synthetic spell", "RELATED") in seizure.synonyms
    onto = HpoOntology.from_document(doc, "sha")
    assert onto.resolve("HP:9000099") == "HP:9000001" and onto.resolve("HP:9000002") == "HP:9000001"
    assert onto.is_phenotypic("HP:9000001")


def test_gencc_rows_keep_classification_verbatim_and_only_selected_diseases():
    header = ",".join(["sgc_id", *bulk.GENCC_FIELDS[1:-1], "submitted_mondo_curie"])
    rows = [["SGC-1", "HGNC:1", "GENEA", "MONDO:1", "d1", "OMIM:1", "Limited", "AD", "Sub", "2020", "", ""],
            ["SGC-2", "HGNC:1", "GENEA", "MONDO:2", "d2", "OMIM:2", "Definitive", "AD", "Sub", "2020", "", ""],
            ["SGC-3", "HGNC:2", "OTHER", "MONDO:1", "d1", "OMIM:1", "Definitive", "AD", "Sub", "2020", "", ""]]
    text = header + "\n" + "\n".join(",".join(r) for r in rows) + "\n"
    selected = bulk.gencc_rows_for(text, {"GENEA"})
    assert [r["sgc_id"] for _, r in selected] == ["SGC-1", "SGC-2"]
    document = bulk.bulk_document(CAP, title="t", source_kind="curation_database", release=None, external_ref="GENCC:t")
    out = bulk.gencc_assertions(document, selected, {"HGNC:1": "HGNC:1"}, {"MONDO:1", "MONDO:2"})
    validity = {e.source_native_validity for e in out.evidence}
    assert validity == {"Limited", "Definitive"}  # verbatim, including non-positive classes
    assert all(a.statement_status == "listed_record" for a in out.assertions)


def test_orphanet_effect_only_from_the_controlled_association_type():
    xml = b"""<JDBOR date="2026-06-23"><DisorderList>
      <Disorder><OrphaCode>1</OrphaCode><Name>Disorder one</Name><DisorderGeneAssociationList>
        <DisorderGeneAssociation><SourceOfValidation>1[PMID]</SourceOfValidation><Gene><Symbol>GENEA</Symbol>
          <ExternalReferenceList><ExternalReference><Source>HGNC</Source><Reference>1</Reference></ExternalReference></ExternalReferenceList></Gene>
          <DisorderGeneAssociationType><Name>Disease-causing germline mutation(s) (gain of function) in</Name></DisorderGeneAssociationType>
          <DisorderGeneAssociationStatus><Name>Assessed</Name></DisorderGeneAssociationStatus></DisorderGeneAssociation>
      </DisorderGeneAssociationList></Disorder>
      <Disorder><OrphaCode>2</OrphaCode><Name>Disorder two gain of function mentioned in name</Name><DisorderGeneAssociationList>
        <DisorderGeneAssociation><Gene><Symbol>GENEA</Symbol></Gene>
          <DisorderGeneAssociationType><Name>Disease-causing germline mutation(s) in</Name></DisorderGeneAssociationType>
          <DisorderGeneAssociationStatus><Name>Assessed</Name></DisorderGeneAssociationStatus></DisorderGeneAssociation>
      </DisorderGeneAssociationList></Disorder></DisorderList></JDBOR>"""
    release, assocs = bulk.orphanet_associations(xml, {"GENEA"})
    assert release == "2026-06-23" and len(assocs) == 2
    _, sssom = parse_sssom("subject_id\tpredicate_id\tobject_id\nMONDO:1\tskos:exactMatch\tOrphanet:1\n"
                           "MONDO:2\tskos:exactMatch\tOrphanet:2\nMONDO:3\tskos:broadMatch\tOrphanet:2\n")
    mapping = bulk.orpha_to_mondo(sssom)
    assert mapping == {"ORPHA:1": ["MONDO:1"], "ORPHA:2": ["MONDO:2"]}  # broad mappings never count
    document = bulk.bulk_document(CAP, title="t", source_kind="curation_database", release=release, external_ref="O:t")
    out, proposals, unmapped = bulk.orphanet_records(document, assocs, {"HGNC:1": "HGNC:1", "GENEA": "HGNC:1"}, mapping)
    assert [(p["disease_id"], p["effect"]) for p in proposals] == [("MONDO:1", "gain")]  # never from free-text names
    assert len(out.assertions) == 2 and not unmapped


def test_reporter_identity_uses_profile_ids_and_mentions_assert_nothing():
    projects = [{"appl_id": 1, "project_num": "R01NS1", "fiscal_year": 2026, "project_title": "Study of GENEA channels",
                 "abstract_text": "We examine GENEA and GENEAB variants.", "is_active": True,
                 "organization": {"org_name": "SYNTHETIC UNIVERSITY", "external_org_id": 7},
                 "agency_ic_admin": {"abbreviation": "NINDS", "name": "Synthetic institute"},
                 "principal_investigators": [{"profile_id": 42, "full_name": "Pat Example", "is_contact_pi": True},
                                             {"profile_id": None, "full_name": "No Id"}]}]
    out = bulk.reporter_records(CAP, projects, {"GENEA": "HGNC:1", "GENEB": "HGNC:2"})
    predicates = sorted({a.predicate for a in out.assertions})
    assert predicates == ["funds", "investigator_on", "mentions", "owns_or_runs", "professional_at"]
    people = [e for e in out.entities if e.type == "person"]
    assert [p.id for p in people] == ["person:nih-reporter-42"]  # a name without an identifier is not a person node
    mention = next(a for a in out.assertions if a.predicate == "mentions")
    assert mention.object_id == "HGNC:1"  # exact symbol on token boundaries: GENEAB does not match GENEB or GENEA
    assert bulk.symbol_spans("GENEAB GENEA, xGENEA", "GENEA") == [(7, 12)]


def test_gaf_reader_keeps_only_selected_biological_process_rows():
    lines = ["!gaf-version: 2.2",
             "UniProtKB\tP1\tGENEA\tinvolved_in\tGO:1\tPMID:1\tIDA\t\tP\tn\t\tprotein\ttaxon:9606\t20260101\tSRC\t\t",
             "UniProtKB\tP1\tGENEA\tenables\tGO:2\tPMID:1\tIDA\t\tF\tn\t\tprotein\ttaxon:9606\t20260101\tSRC\t\t",
             "UniProtKB\tP2\tOTHER\tinvolved_in\tGO:3\tPMID:1\tIDA\t\tP\tn\t\tprotein\ttaxon:9606\t20260101\tSRC\t\t"]
    rows = bulk.gaf_rows_for(gzip.compress("\n".join(lines).encode()), {"GENEA"})
    assert [(n, c[4]) for n, c in rows] == [(2, "GO:1")]
    document = bulk.bulk_document(CAP, title="t", source_kind="curation_database", release=None, external_ref="GO:t")
    out = bulk.go_assertions(document, rows, {"GENEA": "HGNC:1"}, {})
    assert [a.predicate for a in out.assertions] == ["gene_involved_in_process"]
    assert out.entities[0].label == "GO:1"  # no invented label without the GO ontology


@pytest.mark.master("T27", boundary="offline-unit")
def test_obsolete_mondo_ids_resolve_only_through_a_single_replacement():
    from atlas.real_data import resolve_mondo
    from atlas.sources.obo import parse_obo

    mondo = parse_obo("[Term]\nid: MONDO:1\nname: current\n\n[Term]\nid: MONDO:2\nname: obsolete a\nis_obsolete: true\n"
                      "replaced_by: MONDO:1\n\n[Term]\nid: MONDO:3\nname: obsolete b\nis_obsolete: true\n")
    assert resolve_mondo(mondo, "MONDO:2") == "MONDO:1"
    assert resolve_mondo(mondo, "MONDO:3") is None and resolve_mondo(mondo, "MONDO:9") is None
