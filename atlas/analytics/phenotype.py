"""Deterministic two-level phenotype comparison (master plan section 9.1).

Disease baselines and subgroup profiles are separate inputs and separate
results; they are never mixed or unioned. Weighted Jaccard over ancestor-closed
term sets with OMIM-reference information content:

    score = sum(IC(t) for t in A ∩ B) / sum(IC(t) for t in A ∪ B)

The score is null (unavailable, never zero) when either side lacks a usable
positive profile, when any direct term lacks IC-reference support, when an
unresolved positive/negative disagreement exists, when the reference cannot be
loaded, or when the weighted union is zero. Ancestors never count as observed
evidence and do not make an unsupported direct term assessed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from atlas.hashing import content_sha256
from atlas.models.records import Calculation, PhenotypeResult, SharedTermResult, TermCoverage
from atlas.sources.hpo import ROOT_TERMS, HpoOntology, IcReference

ALGORITHM_VERSION = "phenotype-weighted-jaccard-1"
SPECIFIC_IC_CUTOFF = math.log(10)  # term present in at most 10% of reference diseases
SPARSE_DIRECT_TERMS = 5            # fixed presentation heuristic, frozen with the algorithm
CALC_KIND = "phenotype_weighted_jaccard"


@dataclass
class Profile:
    """Eligible positive inputs for one context at one profile level."""

    context_id: str
    disease_id: str
    level: str                                   # disease_baseline | subgroup
    direct: dict[str, set[str]] = field(default_factory=dict)       # term -> input assertion IDs
    evidence_ids: set[str] = field(default_factory=set)
    publication_families: set[str] = field(default_factory=set)
    mapped_annotation_ids: set[str] = field(default_factory=set)
    unresolved_disagreements: set[str] = field(default_factory=set)  # terms with accepted positive and NOT rows
    available: bool = True                        # False: this level does not exist for the context

    def assertion_ids(self) -> list[str]:
        return sorted({a for ids in self.direct.values() for a in ids})


def merge_shared_cohorts(families: dict[str, set[str]]) -> set[str]:
    """Publication families that share a study/cohort ID count once (union-find over shared cohorts).

    Related preprint versions and their article already share one family ID; a
    shared cohort additionally prevents counting repeated reports as independent.
    """
    parent = {f: f for f in families}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    owner: dict[str, str] = {}
    for family, cohorts in sorted(families.items()):
        for cohort in sorted(cohorts):
            if cohort in owner:
                a, b = find(owner[cohort]), find(family)
                if a != b:
                    parent[max(a, b)] = min(a, b)
            else:
                owner[cohort] = family
    return {find(f) for f in families}


def coverage(profile: Profile, reference: IcReference | None) -> TermCoverage:
    direct = sorted(profile.direct)
    unassessed = [t for t in direct if reference is None or reference.ic(t) is None]
    assessed = len(direct) - len(unassessed)
    return TermCoverage(direct_terms=len(direct), assessed_terms=assessed,
                        fraction=(assessed / len(direct)) if direct else None, unassessed_term_ids=unassessed)


def _expanded(profile: Profile, ontology: HpoOntology) -> dict[str, set[str]]:
    """Ancestor-closed term set (roots excluded) -> supporting input assertion IDs."""
    out: dict[str, set[str]] = {}
    for term, assertions in profile.direct.items():
        for ancestor in ontology.ancestors(term):
            if ancestor not in ROOT_TERMS:
                out.setdefault(ancestor, set()).update(assertions)
    return out


def compare(a: Profile, b: Profile, ontology: HpoOntology, reference: IcReference | None,
            labels: dict[str, str]) -> PhenotypeResult:
    level = a.level
    if a.level != b.level:
        raise ValueError("profile levels must match; baseline and subgroup are never mixed")
    missing: list[str] = []
    warnings: list[dict[str, str]] = []
    cov_a, cov_b = coverage(a, reference), coverage(b, reference)
    exp_a, exp_b = _expanded(a, ontology), _expanded(b, ontology)
    shared = sorted(set(exp_a) & set(exp_b))
    union = set(exp_a) | set(exp_b)

    usable = True
    for side, profile in (("a", a), ("b", b)):
        if not profile.available:
            missing.append(f"Side {side} has no {level.replace('_', ' ')} profile level.")
            usable = False
        elif not profile.direct:
            missing.append(f"Scoped phenotype evidence missing on side {side}." if level == "subgroup"
                           else f"No usable positive disease-baseline annotations on side {side}.")
            usable = False
        if profile.unresolved_disagreements:
            missing.append(f"Side {side}: unresolved positive/NOT disagreement for "
                           f"{', '.join(sorted(profile.unresolved_disagreements))}.")
            usable = False
    if reference is None:
        missing.append("IC reference unavailable: no numerical score; exact shared terms shown only.")
        usable = False
    else:
        unassessed = cov_a.unassessed_term_ids + cov_b.unassessed_term_ids
        if unassessed:
            missing.append(f"{len(set(unassessed))} direct term(s) lack support in the IC reference: "
                           f"{', '.join(sorted(set(unassessed)))}.")
            warnings.append({"code": "REFERENCE_INCOMPLETE", "message": "Numerical score and ranking withheld at this level."})
            usable = False

    score: float | None = None
    union_weight = 0.0
    if usable and reference is not None:
        union_weight = sum(reference.ic(t) or 0.0 for t in union)
        if union_weight <= 0:
            missing.append("Weighted union is zero; similarity is unavailable.")
        else:
            score = sum(reference.ic(t) or 0.0 for t in shared) / union_weight

    shared_terms = []
    for term in shared:
        ic = reference.ic(term) if reference is not None else None
        shared_terms.append(SharedTermResult(
            id=term, label=labels.get(term) or ontology.label(term) or term, ic=ic,
            score_contribution=(ic or 0.0) / union_weight if score is not None else None,
            direct_a=term in a.direct, direct_b=term in b.direct,
            input_assertion_ids=sorted(exp_a[term] | exp_b[term])))
    shared_terms.sort(key=lambda s: (-(s.ic if s.ic is not None else -1.0), s.id))
    specific = sorted(s.id for s in shared_terms if s.ic is not None and s.ic >= SPECIFIC_IC_CUTOFF)
    if shared_terms and not specific:
        warnings.append({"code": "BROAD_OVERLAP_ONLY",
                         "message": "No shared term reaches the fixed specificity cutoff (IC >= ln 10); overlap is broad."})
    if a.direct and b.direct and min(len(a.direct), len(b.direct)) < SPARSE_DIRECT_TERMS:
        warnings.append({"code": "SPARSE_PROFILE",
                         "message": f"Sparse profiles ({len(a.direct)} and {len(b.direct)} direct terms); "
                                    "a score describes these few records only, not clinical confidence."})
    return PhenotypeResult(
        score=score, calculation_id=None, profile_level=level, same_parent_disease=a.disease_id == b.disease_id,
        direct_annotation_counts={"a": len(a.direct), "b": len(b.direct)},
        publication_counts={"a": len(a.publication_families), "b": len(b.publication_families)},
        mapped_annotation_ids={"a": sorted(a.mapped_annotation_ids), "b": sorted(b.mapped_annotation_ids)},
        input_assertion_ids={"a": a.assertion_ids(), "b": b.assertion_ids()},
        reference_coverage={"a": cov_a, "b": cov_b}, shared_terms=shared_terms, specific_shared_terms=specific,
        missingness=missing, warnings=warnings,
    )


def has_candidate_overlap(result: PhenotypeResult) -> bool:
    """Permissive browsing filter: a shared non-root term with positive weight (or explicit term if scoreless)."""
    if result.score is not None:
        return any((t.ic or 0) > 0 for t in result.shared_terms)
    return bool(result.shared_terms)


def calculation(result: PhenotypeResult, a: Profile, b: Profile, reference: IcReference | None,
                reference_source_ids: list[str]) -> Calculation:
    parameters = {
        "profile_level": result.profile_level, "context_a": a.context_id, "context_b": b.context_id,
        "ic_reference": "OMIM-only positive aspect-P diseases in pinned phenotype.hpoa",
        "ic_reference_release": str(reference.release) if reference else "unavailable",
        "ic_reference_sha256": reference.source_sha256 if reference else "unavailable",
        "ic_reference_disease_count": str(reference.disease_count) if reference else "0",
        "specific_ic_cutoff": f"{SPECIFIC_IC_CUTOFF:.6f}", "sparse_direct_terms": str(SPARSE_DIRECT_TERMS),
        "excluded_terms": ",".join(sorted(ROOT_TERMS)),
    }
    body = {
        "kind": CALC_KIND, "algorithm_version": ALGORITHM_VERSION, "parameters": parameters,
        "input_assertion_ids": sorted(set(a.assertion_ids()) | set(b.assertion_ids())),
        "input_evidence_ids": sorted(a.evidence_ids | b.evidence_ids),
        "reference_source_ids": sorted(reference_source_ids) if reference else [],
        "result": result.model_dump(mode="json"), "missingness": result.missingness,
    }
    digest = content_sha256(body)
    calc_id = f"calc:{digest[:32]}"
    final = result.model_copy(update={"calculation_id": calc_id})
    return Calculation(id=calc_id, kind=CALC_KIND, algorithm_version=ALGORITHM_VERSION, parameters=parameters,
                       input_assertion_ids=body["input_assertion_ids"], input_evidence_ids=body["input_evidence_ids"],
                       reference_source_ids=body["reference_source_ids"],
                       result=PhenotypeResult.model_validate(final.model_dump(mode="json")),
                       missingness=result.missingness, content_sha256=digest)
