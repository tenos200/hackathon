"""Semantics preservation, review gates, conflicts, catalog freeze and publication rules (offline-unit).

Model outputs here are scripted recordings of synthetic texts. They test pipeline
behavior (statuses are preserved, nothing is reclassified by keywords), not
model reasoning quality, which needs a separate live evaluation.
"""

from __future__ import annotations

import json

import pytest

from atlas.assembly.assemble import AssemblyError, assemble
from atlas.checking.structural import check_assertion
from atlas.cli import assembly_inputs
from atlas.models.io import read_jsonl, write_jsonl
from atlas.models.records import (
    Conflict, Entity, OpportunityRecord, Review, Scope,
)
from atlas.review.workflow import catalog_impact, catalog_manifest, fingerprint
from atlas.sources.builders import make_assertion

from .conftest import real_store_for
from .synthetic_world import (
    EXTRACTIONS, TEST_REVIEWER, accept_pending, build_full_world, build_sources_and_backbone, run_literature,
)


def _statuses(world):
    return {(c.object_mention, c.statement_status) for c in world.candidates
            if world.ws.catalog().sources[c.source_document_id].external_ref.startswith("PMID")}


@pytest.mark.master("T05", boundary="offline-unit")
def test_recorded_statuses_are_preserved_exactly(world):
    w, package, _ = world
    statuses = _statuses(w)
    assert ("Synthetic ataxia", "negated") in statuses
    assert ("synthetic developmental delay", "planned") in statuses
    assert ("synthetic hypotonia", "inconclusive") in statuses
    assert ("synthetic disorder A", "background") in statuses
    store = real_store_for(package)
    published = {(a.record.object_id, a.record.context_id, a.record.statement_status) for a in store.assertions.values()}
    assert ("HP:9000015", "ctx:syn-a-loss", "negated") in published
    assert ("HP:9000013", "ctx:syn-a-loss", "planned") in published
    assert ("HP:9000014", "ctx:syn-b-loss", "inconclusive") in published
    # None of the non-positive statuses enter a positive profile.
    terms = {t.id for t in store.context("ctx:syn-a-loss").data.subgroup_profile.terms}
    assert "HP:9000015" not in terms and "HP:9000013" not in terms


@pytest.mark.master("T05", boundary="offline-unit")
def test_no_keyword_rule_decides_meaning(tmp_path):
    """Metamorphic check: identical text, different recorded judgments -> outputs follow the judgment."""
    flipped = json.loads(json.dumps(EXTRACTIONS))
    claim = flipped["PMID:99000001"]["claims"][2]  # text says "was not observed"
    claim["statement_status"] = "reported_result"
    abstain = {"abstained": True, "abstention_reason": "Synthetic: insufficient context.", "claims": []}
    world = build_sources_and_backbone(tmp_path / "ws")
    docs = {d.canonical_sha256: d.external_ref for d in world.catalog().sources.values()}

    def script(request):
        ref = docs[request.content_hash]
        return abstain if ref == "PMID:99000002" else flipped[ref]

    run_literature(world, extraction_script=script)
    statuses = {(c.object_mention, c.statement_status) for c in world.candidates}
    assert ("Synthetic ataxia", "reported_result") in statuses  # kept for human review, not "corrected" by a word list
    assert not [c for c in world.candidates
                if world.catalog().sources[c.source_document_id].external_ref == "PMID:99000002"]  # abstention honored


@pytest.mark.master("T06", boundary="offline-unit")
def test_unsupported_qualifier_blocks_and_cannot_be_accepted(tmp_path):
    world = build_sources_and_backbone(tmp_path / "ws")
    run_literature(world)
    catalog = world.catalog()
    seizure = world.assertion_where(predicate="has_phenotype", context_id="ctx:syn-a-loss", object_id="HP:9000011")
    assert not check_assertion(seizure, catalog)
    bad_scope = Scope(**{**seizure.scope.payload(), "age_text": "aged 2 to 5 years"})  # not in the source
    bad = make_assertion(subject_id=seizure.subject_id, predicate=seizure.predicate, object_id=seizure.object_id,
                         context_id=seizure.context_id, scope=bad_scope, effect_direction=seizure.effect_direction,
                         statement_status=seizure.statement_status, origin=seizure.origin, support_ids=[])
    support = catalog.supports[seizure.support_ids[0]].model_copy(update={"assertion_id": bad.id})
    bad = bad.model_copy(update={"support_ids": [support.id]})
    catalog.assertions[bad.id] = bad
    catalog.supports[support.id] = support
    problems = check_assertion(bad, catalog)
    assert any("age_text 'aged 2 to 5 years' is not present verbatim" in p for p in problems)
    # A patient count that does not appear in the cited context also blocks.
    evidence = catalog.evidence[support.evidence_id]
    wrong_count = evidence.model_copy(update={"patient_count": 13})
    from atlas.checking.structural import check_evidence
    assert any("patient count 13" in p for p in check_evidence(wrong_count, catalog.sources[evidence.source_document_id]))
    assert not check_evidence(evidence, catalog.sources[evidence.source_document_id])  # 12 is in the text


@pytest.mark.master("T08", boundary="offline-unit")
def test_pending_and_structurally_failed_records_are_not_published(tmp_path):
    world = build_sources_and_backbone(tmp_path / "ws")
    run_literature(world)
    # Accept everything except the subgroup seizure assertion.
    seizure = world.assertion_where(predicate="has_phenotype", context_id="ctx:syn-a-loss", object_id="HP:9000011")
    accept_pending(world, skip=lambda row: row["target_id"] == seizure.id,
                   disagreement="Synthetic reviewer: background attribution reflected in status.")
    package, report = assemble(assembly_inputs(world.ws))
    store = real_store_for(package)
    assert seizure.id not in store.assertions and "review state pending" in report.withheld[seizure.id]
    protocol_claim = [a for a in world.catalog().assertions.values()
                      if a.statement_status == "reported_result" and a.context_id is None
                      and a.origin == "literature_extraction" and a.object_id == "HP:9000011"]
    assert protocol_claim and protocol_claim[0].id not in store.assertions  # structural failure
    assert any(r.startswith("structural") for r in report.withheld[protocol_claim[0].id])


@pytest.mark.master("T08", boundary="offline-unit")
def test_changed_dependency_invalidates_reviews_and_is_listed_before_publication(tmp_path):
    world, _, _ = build_full_world(tmp_path / "ws")
    # Re-capture the gamma lab page with different text: the early manual mechanism import depends on it.
    from atlas.models.io import read_jsonl as rj
    from atlas.models.records import SourceDocument
    path = world.ws.stage_file("documents", "sources")
    docs = rj(path, SourceDocument)
    changed = []
    for d in docs:
        if d.external_ref.endswith("/gamma-lab"):
            d = d.model_copy(update={"title": "Synthetic Gamma Lab (renamed)"})
        changed.append(d)
    write_jsonl(path, changed)
    with pytest.raises(AssemblyError) as error:
        assemble(assembly_inputs(world.ws))
    message = str(error.value)
    assert "stale" in message and "context:ctx:syn-a-loss" in message and "context:ctx:syn-a-gain" in message


@pytest.mark.master("T08", boundary="offline-unit")
def test_review_import_requires_actual_decisions_current_hashes_and_disagreement_reasons(tmp_path):
    from atlas.checking.structural import structural_report
    from atlas.review.workflow import export_queue, import_decisions

    world = build_sources_and_backbone(tmp_path / "ws")
    run_literature(world)
    catalog = world.catalog()
    structural = structural_report(catalog)
    export_queue(catalog, [], structural, world.ws.checks(), world.ws.review)
    template = [json.loads(l) for l in (world.ws.review / "decisions.template.jsonl").read_text().splitlines()]
    background = world.assertion_where(predicate="gene_associated_with_disease", origin="literature_extraction")
    protocol = next(a for a in catalog.assertions.values() if a.id in structural)
    rows = []
    for row in template:
        if row["target_id"] == background.id:
            rows.append(row | {"reviewer": TEST_REVIEWER, "reviewed_at": "2026-10-03T12:00:00Z", "decision": "accepted",
                               "reason": "synthetic", "dimensions": row["dimensions"]})  # failed model check, no reason
        elif row["target_id"] == protocol.id:
            rows.append(row | {"reviewer": TEST_REVIEWER, "reviewed_at": "2026-10-03T12:00:00Z", "decision": "accepted",
                               "reason": "synthetic", "dimensions": row["dimensions"]})
        else:
            rows.append(row)  # untouched template line: no human decision recorded
    stale = dict(template[0]) | {"target_content_sha256": "0" * 64, "reviewer": TEST_REVIEWER,
                                 "reviewed_at": "2026-10-03T12:00:00Z", "decision": "accepted", "reason": "x"}
    rows.append(stale)
    path = tmp_path / "decisions.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    report = import_decisions(path, catalog, structural, world.ws.checks())
    assert not report.imported
    reasons = " | ".join(r for _, r in report.rejected)
    assert "no human decision recorded" in reasons
    assert "model check did not pass" in reasons
    assert "structural failures cannot be overridden" in reasons
    assert "stale" in reasons


@pytest.mark.master("T09", boundary="offline-unit")
def test_conflict_scope_and_disposition(tmp_path):
    world, _, _ = build_full_world(tmp_path / "ws")
    catalog = world.catalog()
    positive = world.assertion_where(subject_id="MONDO:9900005", statement_status="listed_record")
    negative = world.assertion_where(subject_id="MONDO:9900005", statement_status="negated")
    # Without a disposition both are withheld (no unqualified public assertion).
    package, report = assemble(assembly_inputs(world.ws))
    assert positive.id in report.withheld and negative.id in report.withheld
    evidence = sorted({catalog.supports[s].evidence_id for a in (positive, negative) for s in a.support_ids})
    for comparability, expect_in_profile in (("different_context", True), ("unclear", False)):
        conflict = Conflict(id=f"conflict:syn-e-{comparability}", assertion_ids=sorted([positive.id, negative.id]),
                            comparability=comparability, reason=f"Synthetic disposition: {comparability}.",
                            evidence_ids=evidence, review_state="pending", review_id=None)
        write_jsonl(world.ws.imports / "conflicts.jsonl", [conflict])
        audit = world.ws.config_json("context_mapping_audit.json")
        if not any(r["context_id"] == "ctx:syn-e" for r in audit["contexts"]):
            audit["contexts"].append({"context_id": "ctx:syn-e", "intended_population": "synthetic", "mondo_id": "MONDO:9900005",
                                      "source_supported_granularity": "disease", "exact_mapped_ids": ["OMIM:900011"],
                                      "hpo_annotation_rows": 2, "baseline_available": True,
                                      "justification_source_ids": ["synthetic"]})
            (world.ws.config / "context_mapping_audit.json").write_text(json.dumps(audit))
            from atlas.models.records import ResearchContext
            contexts = read_jsonl(world.ws.imports / "contexts.jsonl", ResearchContext)
            contexts.append(ResearchContext(id="ctx:syn-e", gene_ids=[], disease_id="MONDO:9900005", mechanism_id=None,
                                            profile_level="disease", label="Synthetic disorder E", scope="All people with synthetic disorder E.",
                                            definition_evidence_ids=[], definition_assertion_ids=[],
                                            definition_review_state="pending"))
            write_jsonl(world.ws.imports / "contexts.jsonl", contexts)
        accept_pending(world)
        package, report = assemble(assembly_inputs(world.ws))
        store = real_store_for(package)
        view = store.assertion(positive.id).data
        assert [c.comparability for c in view.conflicts] == [comparability]
        labels = {b.label for b in view.badges if b.kind == "conflict"}
        assert labels == ({"Results in different contexts"} if comparability == "different_context" else {"Conflicting accounts"})
        terms = {t.id for t in store.context("ctx:syn-e").data.disease_profile.terms}
        assert ("HP:9000014" in terms) is expect_in_profile


@pytest.mark.master("T04", boundary="offline-unit")
def test_scope_differences_keep_assertions_separate():
    base = dict(subject_id="MONDO:9900001", predicate="has_phenotype", object_id="HP:9000011", context_id="ctx:x",
                effect_direction="not_applicable", statement_status="reported_result", origin="manual_import", support_ids=[])
    empty = Scope.empty().payload()
    ids = {make_assertion(scope=Scope(**{**empty, **delta}), **base).id for delta in (
        {}, {"age_text": "aged 1-3 years"}, {"age_text": "adults"}, {"variant_ids": ["variant:1"]},
        {"outcome_text": "seizure frequency"}, {"population_text": "cohort 1"})}
    assert len(ids) == 6
    # Set-like lists are order-insensitive; wording is not paraphrased.
    a = make_assertion(scope=Scope(**{**empty, "other_qualifiers": ["x", "y"]}), **base).id
    b = make_assertion(scope=Scope(**{**empty, "other_qualifiers": ["y", "x"]}), **base).id
    assert a == b


@pytest.mark.master("T03", boundary="offline-unit")
def test_multi_span_citation_inside_context_with_unicode(world):
    _, package, _ = world
    store = real_store_for(package)
    seizure = next(a for a in store.assertions.values()
                   if a.record.context_id == "ctx:syn-a-loss" and a.record.object_id == "HP:9000011")
    view = store.assertion(seizure.record.id).data
    text_evidence = [e for e in view.evidence if e.kind == "text_spans"]
    assert len(text_evidence) == 1
    context = text_evidence[0].context
    assert len(context.text) == context.canonical_end - context.canonical_start
    assert len(context.highlights) == 2
    for h in context.highlights:
        assert context.text[h.start:h.end] == h.quote  # code-point offsets
    assert context.text.startswith("🧬")  # a non-BMP character precedes both quotes
    first = context.highlights[0]
    assert context.text.encode("utf-16-le")[: first.start * 2].decode("utf-16-le", "ignore") != context.text[: first.start]
    assert "12 individuals" in context.text  # the necessary population qualifier is inside the shown context
    source = text_evidence[0].source
    assert source.public_text_policy == "full" and source.metadata.publication_family_id == "family:pmid:99000001"


@pytest.mark.master("T03", boundary="offline-unit")
def test_link_only_sources_redistribute_no_text(world):
    _, package, _ = world
    store = real_store_for(package)
    planned = next(a for a in store.assertions.values() if a.record.statement_status == "planned"
                   and a.record.context_id is None)
    view = store.assertion(planned.record.id).data
    assert all(e.context is None and e.source.public_text_policy == "link_only" for e in view.evidence)
    raw = json.dumps(package)
    assert "We will record synthetic seizure frequency" not in raw


@pytest.mark.master("T15", boundary="offline-unit")
def test_reuse_confirmed_requires_permission_and_qualified_review(tmp_path):
    world, _, _ = build_full_world(tmp_path / "ws")
    path = world.ws.imports / "opportunities.jsonl"
    opp = read_jsonl(path, OpportunityRecord)[0]
    write_jsonl(path, [opp.model_copy(update={"readiness": "reuse_confirmed"})])
    accept_pending(world)
    with pytest.raises(AssemblyError, match="reuse_confirmed requires explicit access permission"):
        assemble(assembly_inputs(world.ws))


@pytest.mark.master("T15", boundary="offline-unit")
def test_exact_disease_assets_first_and_leads_stay_questions(world):
    _, package, _ = world
    store = real_store_for(package)
    unknown = store.actions("ctx:syn-a-unknown").data
    assert [a.id for a in unknown.exact_disease_assets] == ["asset:syn-a-registry"]
    registry = unknown.exact_disease_assets[0]
    assert registry.owner.id == "org:syn-alpha" and registry.access_status == "unknown"
    lead = store.actions("ctx:syn-a-loss").data
    # The registry's intended scope is the whole disease, so it is an exact-disease asset for the subgroup too.
    assert [a.id for a in lead.exact_disease_assets] == ["asset:syn-a-registry"]
    opportunity = lead.opportunities[0]
    assert opportunity.readiness == "investigate_compatibility" and opportunity.asset.access_status == "unknown"
    assert opportunity.action.opportunity_ids == [opportunity.id]
    assert set(opportunity.comparison_ids) <= {c.id for c in store.c.comparisons}


@pytest.mark.master("T16", boundary="offline-unit")
def test_incomplete_coverage_never_becomes_global_absence(tmp_path):
    world, package, _ = build_full_world(tmp_path / "ws")
    gap = real_store_for(package).actions("ctx:syn-c-group").data.gaps[0]
    assert gap.kind == "incomplete_retrieval" and gap.coverage[0].completion == "failed"
    assert "does not exist" not in gap.description and "these inspected sources" in gap.description
    from atlas.models.records import GapRecord
    path = world.ws.imports / "gaps.jsonl"
    g = read_jsonl(path, GapRecord)[0]
    write_jsonl(path, [g.model_copy(update={"kind": "not_found_in_search"})])
    with pytest.raises(AssemblyError, match="cannot support not_found_in_search"):
        assemble(assembly_inputs(world.ws))


@pytest.mark.master("T17", boundary="offline-unit")
def test_explanations_bound_to_reviewed_dependency_hashes(tmp_path):
    world, package, _ = build_full_world(tmp_path / "ws")
    store = real_store_for(package)
    summary = store.context("ctx:syn-a-loss").data.summary
    assert summary[0].text.startswith("One synthetic case series")  # reviewed synthesis is served verbatim
    for card in store.connections("ctx:syn-a-loss").data.comparisons:
        for score in (card.disease_baseline, card.subgroup_comparison):
            assert score.calculation_id in store.calculations
    graph = store.connections("ctx:syn-a-loss").data.graph
    assert graph and all(l.computed and l.calculation_id in store.calculations for l in graph.links)
    # Change one dependency: the reviewed explanation is withheld and the deterministic template is used.
    from atlas.models.records import Explanation
    path = world.ws.imports / "explanations.jsonl"
    x = read_jsonl(path, Explanation)[0]
    tampered = dict(x.dependency_hashes)
    tampered[next(iter(tampered))] = "0" * 64
    write_jsonl(path, [x.model_copy(update={"dependency_hashes": tampered})])
    accept_pending(world)  # even a fresh acceptance cannot revive mismatched dependencies
    package2, report = assemble(assembly_inputs(world.ws))
    assert "dependency hashes changed" in " ".join(report.withheld[x.id])
    fallback = real_store_for(package2).context("ctx:syn-a-loss").data.summary
    assert fallback and all(len(s.assertion_ids) == 1 for s in fallback)  # one record per template sentence


@pytest.mark.master("T28", boundary="offline-unit")
def test_catalog_freeze_impact_lists_and_blocks_stale_dependents(tmp_path):
    world, _, _ = build_full_world(tmp_path / "ws")
    frozen = catalog_manifest(world.catalog())
    from atlas.cli import _all_reviews
    # Appending an unrelated entity does not invalidate anything.
    from atlas.models.records import Entity as E
    extra = E(id="org:syn-unrelated", type="organization", label="Unrelated", aliases=[], external_ids=[],
              properties={"official_url": "https://x.invalid", "contact_url": None, "domain": None}, identity_source_ids=[])
    path = world.ws.stage_file("entities", "backbone")
    original = read_jsonl(path, E)
    write_jsonl(path, [*original, extra])
    impact = catalog_impact(world.catalog(), frozen, _all_reviews(world.ws))
    assert impact["appended_entity_ids"] == ["org:syn-unrelated"] and not impact["stale_accepted_reviews"]
    assemble(assembly_inputs(world.ws))
    # Editing an alias of disease A after the freeze lists the affected work and blocks publication.
    sources_entities = world.ws.stage_file("entities", "sources")
    entities = read_jsonl(sources_entities, E)
    edited = [e.model_copy(update={"aliases": [*e.aliases, e.aliases[0].model_copy(update={"text": "late alias"})]})
              if e.id == "MONDO:9900001" else e for e in entities]
    write_jsonl(sources_entities, edited)
    impact = catalog_impact(world.catalog(), frozen, _all_reviews(world.ws))
    # The disease and every context defined on it changed; nothing else did.
    assert impact["changed_catalog_ids"] == ["MONDO:9900001", "ctx:syn-a-gain", "ctx:syn-a-loss", "ctx:syn-a-unknown"]
    assert impact["affected_assertion_ids"] and any(s.startswith("context:ctx:syn-a") for s in impact["stale_accepted_reviews"])
    with pytest.raises(AssemblyError, match="stale"):
        assemble(assembly_inputs(world.ws))


@pytest.mark.master("T29", boundary="offline-unit")
def test_mechanism_context_needs_accepted_source_spanned_definition(tmp_path):
    world = build_sources_and_backbone(tmp_path / "ws")
    # Accept everything except the gain-of-function context definition.
    accept_pending(world, skip=lambda row: row["target_id"] == "ctx:syn-a-gain")
    package, report = assemble(assembly_inputs(world.ws))
    store = real_store_for(package)
    assert "ctx:syn-a-gain" not in store.contexts and "ctx:syn-a-gain" in report.withheld
    assert "ctx:syn-a-loss" in store.contexts and store.context("ctx:syn-a-loss").data.mechanism.effect == "loss"
    definition = store.assertion(store.context("ctx:syn-a-loss").data.definition_assertion_ids[0]).data
    assert definition.origin == "manual_import" and definition.evidence[0].context.highlights
    # No subgroup phenotype is inferred from a mechanism definition.
    assert store.context("ctx:syn-a-loss").data.subgroup_profile.terms == []
    # The disease-wide unknown-mechanism journey still works.
    assert store.actions("ctx:syn-a-unknown").data.exact_disease_assets


@pytest.mark.master("T33", boundary="offline-unit")
def test_context_audit_required_and_grouping_class_does_not_borrow_a_leaf(tmp_path, world):
    _, package, _ = world
    store = real_store_for(package)
    grouping = store.context("ctx:syn-c-group").data
    assert grouping.disease_profile.terms == [] and any("baseline is unavailable" in l for l in grouping.limitations)
    w2, _, _ = build_full_world(tmp_path / "ws2")
    audit = w2.ws.config_json("context_mapping_audit.json")
    audit["contexts"] = [r for r in audit["contexts"] if r["context_id"] != "ctx:syn-b-loss"]
    (w2.ws.config / "context_mapping_audit.json").write_text(json.dumps(audit))
    with pytest.raises(AssemblyError, match="ctx:syn-b-loss has no complete hour-one context/mapping audit"):
        assemble(assembly_inputs(w2.ws))


@pytest.mark.master("T21", boundary="offline-unit")
def test_public_package_contains_no_private_review_data(world):
    w, package, _ = world
    raw = json.dumps(package)
    assert TEST_REVIEWER not in raw and "Synthetic stand-in decision" not in raw
    assert "review:" not in raw  # private review IDs are not published
    reviews = read_jsonl(w.ws.review / "reviews.jsonl", Review)
    assert reviews and all(r.reviewer == TEST_REVIEWER for r in reviews)
    for a in package["content"]["assertions"]:
        assert set(a["review"]) == {"target_id", "description", "expert_validation", "reviewed_content_sha256"}
        assert a["review"]["expert_validation"] is False
        assert a["review"]["reviewed_content_sha256"] == next(
            r.target_content_sha256 for r in reviews if r.target_id == a["record"]["id"])
