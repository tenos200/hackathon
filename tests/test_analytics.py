"""Deterministic phenotype analytics, mechanism cards and neighborhoods (offline-unit)."""

from __future__ import annotations

import math
import random

import pytest

from atlas.analytics import phenotype
from atlas.analytics.mechanism import MechanismView, compare_mechanisms
from atlas.models.records import Entity
from atlas.sources.hpo import HpoOntology, build_ic_reference, parse_hpoa

from .conftest import real_store_for
from .synthetic_world import SYN, build_sources_and_backbone, accept_pending


@pytest.fixture(scope="module")
def onto_ref():
    ontology = HpoOntology.from_obo((SYN / "hp_synthetic.obo").read_text(), "o")
    reference = build_ic_reference(parse_hpoa((SYN / "phenotype_synthetic.hpoa").read_text(), "h"), ontology)
    return ontology, reference


def profile(terms: list[str], level="subgroup", cid="c", disease="D", available=True) -> phenotype.Profile:
    p = phenotype.Profile(context_id=cid, disease_id=disease, level=level, available=available)
    for i, t in enumerate(terms):
        p.direct.setdefault(t, set()).add(f"assert:{cid}:{i}")
    p.publication_families = {f"family:{cid}"}
    return p


@pytest.mark.master("T11", boundary="offline-unit")
def test_reference_counts_multiple_parents_once_and_excludes_not(onto_ref):
    ontology, reference = onto_ref
    # Eleven OMIM diseases have positive aspect-P rows; ORPHA/DECIPHER rows are not in the reference.
    assert reference.disease_count == 11
    assert ontology.ancestors("HP:9000014") >= {"HP:9000010", "HP:9000020", "HP:0000118"}  # two parents
    # Hypotonia (positive in 900001 and 900011) propagates once to muscle, which also has 900010.
    assert reference.counts["HP:9000020"] == 3
    # The NOT ataxia row of 900001 does not count: only 900002 has positive ataxia.
    assert reference.counts["HP:9000015"] == 1
    assert reference.ic("HP:9000040") is None  # ORPHA-only term: unknown reference support, not infinite IC
    assert reference.ic("HP:0000118") == pytest.approx(0.0)


@pytest.mark.master("T11", boundary="offline-unit")
@pytest.mark.master("T12", boundary="offline-unit")
def test_missing_or_zero_weight_profiles_are_null_not_zero(onto_ref):
    ontology, reference = onto_ref
    empty = phenotype.compare(profile(["HP:9000011"]), profile([]), ontology, reference, {})
    assert empty.score is None and "Scoped phenotype evidence missing on side b." in empty.missingness
    unavailable = phenotype.compare(profile([], level="subgroup", available=False), profile(["HP:9000011"]),
                                    ontology, reference, {})
    assert unavailable.score is None
    # Only the phenotypic-abnormality root shared: weighted union of roots is excluded -> zero union -> null.
    root_only = phenotype.compare(profile(["HP:0000118"]), profile(["HP:0000118"]), ontology, reference, {})
    assert root_only.score is None and any("zero" in m for m in root_only.missingness)
    disjoint = phenotype.compare(profile(["HP:9000016"]), profile(["HP:9000020"]), ontology, reference, {})
    assert disjoint.score == 0.0  # disjoint observed annotations under this calculation, not a proven difference


@pytest.mark.master("T12", boundary="offline-unit")
def test_weighted_overlap_is_symmetric_bounded_and_identity_is_one(onto_ref):
    ontology, reference = onto_ref
    assessed = ["HP:9000011", "HP:9000012", "HP:9000013", "HP:9000014", "HP:9000015", "HP:9000016", "HP:9000020"]
    rng = random.Random(7)
    for _ in range(200):
        a = rng.sample(assessed, rng.randint(1, 4))
        b = rng.sample(assessed, rng.randint(1, 4))
        ab = phenotype.compare(profile(a, cid="a"), profile(b, cid="b"), ontology, reference, {})
        ba = phenotype.compare(profile(b, cid="b"), profile(a, cid="a"), ontology, reference, {})
        assert ab.score is not None and 0.0 <= ab.score <= 1.0
        assert math.isclose(ab.score, ba.score, rel_tol=1e-12)
        assert math.isclose(sum(t.score_contribution or 0 for t in ab.shared_terms), ab.score, abs_tol=1e-12)
    same = phenotype.compare(profile(["HP:9000014", "HP:9000015"], cid="a"), profile(["HP:9000014", "HP:9000015"], cid="b"),
                             ontology, reference, {})
    assert same.score == pytest.approx(1.0)
    expected = sum(reference.ic(t) for t in ("HP:9000010", "HP:9000011")) / sum(
        reference.ic(t) for t in ("HP:9000010", "HP:9000011", "HP:9000014", "HP:9000020", "HP:9000015"))
    mixed = phenotype.compare(profile(["HP:9000011", "HP:9000014"]), profile(["HP:9000011", "HP:9000015"]),
                              ontology, reference, {})
    assert mixed.score == pytest.approx(expected)


@pytest.mark.master("T12", boundary="offline-unit")
@pytest.mark.master("T17", boundary="offline-unit")
def test_calculation_inputs_and_reference_resolve(world):
    _, package, _ = world
    store = real_store_for(package)
    for calc_id, calc in store.calculations.items():
        data = store.calculation(calc_id).data
        assert set(data.input_assertion_ids) <= set(store.assertions)
        assert set(data.input_evidence_ids) <= set(store.evidence)
        if data.result.score is not None:
            assert data.reference_sources, "a numerical score cites its IC reference source"
            params = {p.name: p.value for p in data.parameters}
            assert params["ic_reference_disease_count"] == "11" and params["ic_reference_sha256"]


@pytest.mark.master("T34", boundary="offline-unit")
def test_reference_incomplete_direct_term_withholds_score_and_ranking(onto_ref, world):
    ontology, reference = onto_ref
    result = phenotype.compare(profile(["HP:9000011", "HP:9000040"], cid="d"), profile(["HP:9000011"], cid="a"),
                               ontology, reference, {})
    assert result.score is None
    assert result.reference_coverage["a"].unassessed_term_ids == ["HP:9000040"]
    assert result.reference_coverage["a"].fraction == 0.5 and result.reference_coverage["a"].assessed_terms == 1
    assert any(w["code"] == "REFERENCE_INCOMPLETE" for w in result.warnings)
    assert all(t.score_contribution is None for t in result.shared_terms)
    assert next(t for t in result.shared_terms if t.id == "HP:9000011").ic is not None  # qualitative terms remain
    _, package, _ = world
    store = real_store_for(package)
    card = next(c for c in store.connections("ctx:syn-a-unknown").data.comparisons if c.context_b.id == "ctx:syn-d")
    assert card.ranking_basis == "unranked" and card.disease_baseline.score is None
    assert store.context("ctx:syn-d").data.disease_profile.unassessed_term_ids == ["HP:9000040"]


@pytest.mark.master("T13", boundary="offline-unit")
def test_same_effect_label_never_yields_mechanistic_equivalence():
    def mech(mid, gene, effect, cell):
        return Entity(id=mid, type="mechanism", label=mid, aliases=[], external_ids=[], identity_source_ids=[],
                      properties={"gene_id": gene, "functional_effect": effect, "process_ids": [],
                                  "cell_tissue_text": cell, "assay_species_text": None, "definition_evidence_ids": []})
    a = MechanismView("ctx:a", mech("mech:a", "HGNC:1", "loss", "cortical neurons"), ("assert:a",), {})
    b = MechanismView("ctx:b", mech("mech:b", "HGNC:2", "loss", "cardiac myocytes"), ("assert:b",), {})
    c = MechanismView("ctx:c", mech("mech:c", "HGNC:2", "loss", None), ("assert:c",), {})
    for other in (b, c):
        features = {f.dimension: f for f in compare_mechanisms(a, other)}
        assert features["functional_effect"].comparison == "same"
        assert "does not establish equivalent biology" in features["functional_effect"].explanation
        assert features["gene"].comparison == "different"
        assert features["cell_or_tissue_context"].comparison in ("not_comparable", "unknown")
        assert all("equivalent mechanism" not in f.explanation for f in features.values())
    unknown = compare_mechanisms(a, MechanismView("ctx:u", None, (), {}))
    assert [f.comparison for f in unknown] == ["unknown"] and "not absent" in unknown[0].explanation


@pytest.mark.master("T26", boundary="offline-unit")
def test_same_parent_baseline_shared_but_subgroup_null_without_scoped_literature(tmp_path):
    from atlas.assembly.assemble import assemble
    from atlas.cli import assembly_inputs

    world = build_sources_and_backbone(tmp_path / "ws")
    accept_pending(world)
    before, _ = assemble(assembly_inputs(world.ws))
    store = real_store_for(before)
    card = next(c for c in store.connections("ctx:syn-a-loss").data.comparisons if c.context_b.id == "ctx:syn-a-gain")
    assert card.disease_baseline.score == pytest.approx(1.0) and card.disease_baseline.same_parent_disease
    assert card.subgroup_comparison.score is None and card.ranking_basis == "unranked"  # no baseline fallback
    assert store.context("ctx:syn-a-loss").data.subgroup_profile.terms == []  # no inherited HPO rows
    # Adding scoped literature changes only that subgroup's profile.
    from .synthetic_world import run_literature
    run_literature(world)
    accept_pending(world, disagreement="Synthetic reviewer: background attribution reflected in status.")
    after_pkg, _ = assemble(assembly_inputs(world.ws))
    after = real_store_for(after_pkg)
    assert {t.id for t in after.context("ctx:syn-a-loss").data.subgroup_profile.terms} == {"HP:9000011", "HP:9000014"}
    assert after.context("ctx:syn-a-gain").data.subgroup_profile.terms == []
    assert (after.context("ctx:syn-a-unknown").data.disease_profile ==
            store.context("ctx:syn-a-unknown").data.disease_profile)  # baseline unchanged


@pytest.mark.master("T32", boundary="offline-unit")
def test_neighborhood_ranking_rules_warnings_and_card_cap(world):
    _, package, _ = world
    store = real_store_for(package)
    for context_id in store.contexts:
        data = store.connections(context_id).data
        assert len(data.comparisons) <= 3
        bases = [c.ranking_basis for c in data.comparisons]
        assert bases == sorted(bases, key=lambda b: b == "unranked")  # ranked cards first
        level = store.contexts[context_id].record.profile_level
        for card in data.comparisons:
            if level == "subgroup":
                assert card.ranking_basis in ("subgroup", "unranked")
                if card.ranking_basis == "subgroup":
                    assert card.subgroup_comparison.score is not None
            else:
                assert card.ranking_basis in ("disease_baseline", "unranked")
            for score in (card.disease_baseline, card.subgroup_comparison):
                for term in score.shared_terms:
                    assert (term.id in score.specific_shared_term_ids) == (term.ic is not None and term.ic >= math.log(10))
                if score.shared_terms and not score.specific_shared_term_ids:
                    assert any(w.code == "BROAD_OVERLAP_ONLY" for w in score.warnings)
    loss = store.connections("ctx:syn-a-loss").data.comparisons
    assert [c.context_b.id for c in loss][:1] == ["ctx:syn-b-loss"]  # the only subgroup-ranked candidate
    assert any(c.context_b.id == "ctx:syn-a-gain" and c.ranking_basis == "unranked" for c in loss)


@pytest.mark.master("T35", boundary="offline-unit")
def test_sparse_counts_report_direct_terms_and_publications_without_strength_claims(world):
    _, package, _ = world
    store = real_store_for(package)
    subgroup = store.context("ctx:syn-a-loss").data.subgroup_profile
    assert subgroup.direct_term_count == 2 and subgroup.distinct_publication_count == 1  # ancestors not counted
    card = next(c for c in store.connections("ctx:syn-a-loss").data.comparisons if c.context_b.id == "ctx:syn-a-gain")
    assert card.disease_baseline.score == pytest.approx(1.0)
    assert any(w.code == "SPARSE_PROFILE" for w in card.disease_baseline.warnings)
    texts = [s.text for s in card.summary] + [w.message for w in card.disease_baseline.warnings]
    texts += [s.text for s in store.connections("ctx:syn-a-loss").data.counterexamples]
    assert not any(word in t.lower() for t in texts for word in ("strong", "proven", "established", "confirmed"))


@pytest.mark.master("T10", boundary="offline-unit")
def test_shared_cohort_reports_count_once():
    merged = phenotype.merge_shared_cohorts({"family:pmid:1": {"cohort:x"}, "family:pmid:2": {"cohort:x"},
                                             "family:pmid:3": set(), "family:doi:preprint": {"cohort:y"},
                                             "family:pmid:4": {"cohort:y", "cohort:z"}})
    assert len(merged) == 3


@pytest.mark.master("T10", boundary="offline-unit")
def test_profile_publication_count_merges_cohort_shared_reports(world):
    from atlas.assembly.assemble import assemble
    from atlas.cli import assembly_inputs

    w, package, _ = world
    inputs = assembly_inputs(w.ws)
    # Both scoped subgroup assertions of ctx:syn-a-loss come from one paper and one cohort.
    assert real_store_for(package).context("ctx:syn-a-loss").data.subgroup_profile.distinct_publication_count == 1
    assert inputs.catalog  # rebuild is deterministic
    again, _ = assemble(inputs)
    assert again["snapshot_id"] == package["snapshot_id"]
