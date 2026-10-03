"""Computed neighborhoods: exploratory clustering through explainable similarity neighborhoods.

For a disease-level start, other disease-level contexts are ranked by the
available disease-baseline score (descending, then context ID). For a subgroup
start, other subgroup contexts are ranked only by an available subgroup score;
a missing subgroup score is never replaced by the baseline. Contexts with a
reason to inspect but no ranking score at that level are listed separately as
unranked candidates in stable ID order. At most three cards: ranked first.
The center plus its cards is an overlapping computed neighborhood, not a
validated disease class or mechanistic cluster; the cap is a presentation
limit, not a threshold.
"""

from __future__ import annotations

from dataclasses import dataclass

from atlas.analytics import phenotype
from atlas.analytics.mechanism import MechanismView, compare_mechanisms
from atlas.hashing import stable_id
from atlas.models.records import (
    Calculation, ComparisonRecord, MechanismFeatureRecord, PhenotypeResult, ResearchContext, SentenceRecord,
)
from atlas.sources.hpo import HpoOntology, IcReference

MAX_CARDS = 3


@dataclass
class NeighborhoodResult:
    comparisons: list[ComparisonRecord]
    calculations: list[Calculation]


def _fmt(score: float | None) -> str:
    return "unavailable" if score is None else f"{score:.2f}"


def _summary(result: PhenotypeResult, calc_id: str, label: str) -> SentenceRecord:
    counts = result.direct_annotation_counts
    pubs = result.publication_counts
    text = (f"{label} weighted overlap: {_fmt(result.score)} "
            f"({counts['a']} and {counts['b']} direct terms; {pubs['a']} and {pubs['b']} distinct publication families).")
    if result.score is None and result.missingness:
        text += " " + result.missingness[0]
    return SentenceRecord(text=text, assertion_ids=[], calculation_ids=[calc_id], opportunity_ids=[])


def build_neighborhoods(contexts: list[ResearchContext], baseline: dict[str, phenotype.Profile],
                        subgroup: dict[str, phenotype.Profile], mechanisms: dict[str, MechanismView],
                        ontology: HpoOntology, reference: IcReference | None, labels: dict[str, str],
                        reference_source_ids: list[str]) -> NeighborhoodResult:
    by_id = {c.id: c for c in contexts}
    calculations: dict[str, Calculation] = {}
    comparisons: list[ComparisonRecord] = []
    for start in sorted(contexts, key=lambda c: c.id):
        ranked: list[tuple[float, str, ComparisonRecord]] = []
        unranked: list[tuple[str, ComparisonRecord]] = []
        for other in sorted(contexts, key=lambda c: c.id):
            if other.id == start.id or other.profile_level != start.profile_level:
                continue
            base = phenotype.compare(baseline[start.id], baseline[other.id], ontology, reference, labels)
            sub = phenotype.compare(subgroup[start.id], subgroup[other.id], ontology, reference, labels)
            base_calc = phenotype.calculation(base, baseline[start.id], baseline[other.id], reference, reference_source_ids)
            sub_calc = phenotype.calculation(sub, subgroup[start.id], subgroup[other.id], reference, reference_source_ids)
            features = compare_mechanisms(mechanisms[start.id], mechanisms[other.id])
            level_result = sub if start.profile_level == "subgroup" else base
            basis = "subgroup" if start.profile_level == "subgroup" else "disease_baseline"
            reasons: list[str] = []
            if phenotype.has_candidate_overlap(base):
                reasons.append("shared disease-baseline terms")
            if any(f.comparison in ("same", "not_comparable") for f in features
                   if f.dimension in ("functional_effect", "affected_process")):
                reasons.append("sourced mechanism features")
            ranked_ok = level_result.score is not None and phenotype.has_candidate_overlap(level_result)
            if not ranked_ok and not reasons:
                continue
            differences = [f.explanation for f in features if f.comparison in ("different", "not_comparable")]
            unknowns = [f.explanation for f in features if f.comparison == "unknown"]
            if not ranked_ok:
                why = level_result.missingness[0] if level_result.missingness else "no ranking score at this level"
                unknowns.append(f"Not ranked at the {basis.replace('_', ' ')} level ({why}); "
                                f"listed because of {' and '.join(reasons) or 'phenotype overlap'}.")
            record = ComparisonRecord(
                id=stable_id("comparison", [start.id, other.id, base_calc.id, sub_calc.id]),
                context_a=start.id, context_b=other.id,
                disease_baseline=base_calc.result, subgroup_comparison=sub_calc.result,
                ranking_basis=basis if ranked_ok else "unranked",
                mechanism_features=features, relevant_differences=differences, unknowns=unknowns,
                calculation_ids=sorted({base_calc.id, sub_calc.id}),
                summary=[_summary(base_calc.result, base_calc.id, "Disease-baseline"),
                         _summary(sub_calc.result, sub_calc.id, "Subgroup")],
            )
            calculations[base_calc.id] = base_calc
            calculations[sub_calc.id] = sub_calc
            if ranked_ok:
                ranked.append((level_result.score, other.id, record))
            else:
                unranked.append((other.id, record))
        ranked.sort(key=lambda r: (-r[0], r[1]))
        unranked.sort(key=lambda r: r[0])
        cards = [r[2] for r in ranked] + [r[1] for r in unranked]
        comparisons.extend(cards[:MAX_CARDS])
    used = {cid for c in comparisons for cid in c.calculation_ids}
    del by_id
    return NeighborhoodResult(comparisons=comparisons,
                              calculations=sorted((calculations[c] for c in used), key=lambda c: c.id))


def counterexamples(comparison: ComparisonRecord) -> list[SentenceRecord]:
    """Deterministic descriptions of records that must not be over-read."""
    out: list[SentenceRecord] = []
    base, sub = comparison.disease_baseline, comparison.subgroup_comparison
    if base.same_parent_disease and base.score is not None and sub.score is None:
        out.append(SentenceRecord(
            text=f"The two contexts share one parent-disease baseline (overlap {_fmt(base.score)}); "
                 "this does not establish subgroup similarity, and the subgroup comparison is unavailable.",
            assertion_ids=[], calculation_ids=[c for c in (base.calculation_id, sub.calculation_id) if c],
            opportunity_ids=[]))
    effect = next((f for f in comparison.mechanism_features if f.dimension == "functional_effect"), None)
    if effect is not None and effect.comparison == "different" and base.score is not None and base.score >= 0.5:
        out.append(SentenceRecord(
            text="Phenotype overlap coexists with different recorded functional effects; overlap does not imply a "
                 "shared mechanism.",
            assertion_ids=effect.assertion_ids, calculation_ids=[base.calculation_id] if base.calculation_id else [],
            opportunity_ids=[]))
    return out


def feature_assertions(features: list[MechanismFeatureRecord]) -> list[str]:
    return sorted({a for f in features for a in f.assertion_ids})
