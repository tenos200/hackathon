"""Predicate/type contract (master plan section 5.4).

No other predicates enter the published graph without an explicit contract
change. These checks are structural (types and IDs), not semantic.
"""

from __future__ import annotations

from dataclasses import dataclass

from atlas.models.records import Entity


@dataclass(frozen=True)
class PredicateRule:
    subject_types: frozenset[str]
    object_types: frozenset[str]
    interpretation: str
    object_asset_kind: str | None = None  # required asset kind for the object, if any
    direction_required: bool = False


PREDICATES: dict[str, PredicateRule] = {
    "gene_associated_with_disease": PredicateRule(frozenset({"gene"}), frozenset({"disease"}),
        "Preserve source-native validity; does not imply a mechanism"),
    "variant_in_gene": PredicateRule(frozenset({"variant"}), frozenset({"gene"}), "Identity/location relation"),
    "variant_associated_with_disease": PredicateRule(frozenset({"variant"}), frozenset({"disease"}),
        "Preserve source classification"),
    "variant_has_effect": PredicateRule(frozenset({"variant"}), frozenset({"mechanism"}),
        "Requires functional-effect evidence"),
    "disease_has_mechanism": PredicateRule(frozenset({"disease"}), frozenset({"mechanism"}),
        "Scoped, supported mechanism attribution"),
    "mechanism_causes_disease": PredicateRule(frozenset({"mechanism"}), frozenset({"disease"}),
        "Only when that causal assertion is supported in context"),
    "has_phenotype": PredicateRule(frozenset({"disease"}), frozenset({"phenotype"}),
        "Positive or explicitly negated, never silently inverted"),
    "gene_involved_in_process": PredicateRule(frozenset({"gene"}), frozenset({"process"}),
        "Membership/involvement, not disruption"),
    "mechanism_affects_process": PredicateRule(frozenset({"mechanism"}), frozenset({"process"}),
        "Separately evidenced alteration"),
    "responds_to": PredicateRule(frozenset({"disease", "mechanism"}), frozenset({"asset"}),
        "Asset must be an intervention; direction and outcome required", "intervention", True),
    "studies": PredicateRule(frozenset({"study"}), frozenset({"disease", "mechanism"}),
        "Registered design, not demonstrated effect"),
    "tests": PredicateRule(frozenset({"study"}), frozenset({"asset"}), "Not efficacy", "intervention"),
    "serves": PredicateRule(frozenset({"organization"}), frozenset({"disease"}), "Sourced community remit"),
    "owns_or_runs": PredicateRule(frozenset({"organization"}), frozenset({"asset", "study"}),
        "Distinguish ownership/operation in source metadata"),
    "asset_for_context": PredicateRule(frozenset({"asset"}), frozenset({"disease", "mechanism"}),
        "Intended scope, not reuse in another context"),
    "professional_at": PredicateRule(frozenset({"person"}), frozenset({"organization"}),
        "Verified public professional affiliation"),
    "works_on": PredicateRule(frozenset({"person"}), frozenset({"disease", "mechanism"}),
        "Sourced professional work, not name co-occurrence"),
    # Contract-extension predicates (proposed contract 1.1.0; source-record relations only).
    "mentions": PredicateRule(frozenset({"study", "asset"}), frozenset({"gene", "disease"}),
        "The source text contains the entity's exact approved symbol or label; no relationship is asserted"),
    "investigator_on": PredicateRule(frozenset({"person"}), frozenset({"study"}),
        "Listed as an investigator on a registered or funded project; not expertise or endorsement"),
    "funds": PredicateRule(frozenset({"organization"}), frozenset({"study"}),
        "Recorded funder of a project; not an assessment of the work"),
    "disease_subclass_of": PredicateRule(frozenset({"disease"}), frozenset({"disease"}),
        "Classified under the broader disease in the pinned Mondo release (is_a chain); not shared mechanism"),
}
EXTENSION_PREDICATES = frozenset({"mentions", "investigator_on", "funds", "disease_subclass_of"})


def check_predicate(predicate: str, subject: Entity | None, obj: Entity | None, effect_direction: str,
                    outcome_text: str | None) -> list[str]:
    """Return structural problems; an empty list means the types are valid."""
    rule = PREDICATES.get(predicate)
    if rule is None:
        return [f"predicate {predicate!r} is not in the published contract"]
    problems: list[str] = []
    if subject is None:
        problems.append("subject ID does not resolve in the catalog")
    elif subject.type not in rule.subject_types:
        problems.append(f"{predicate} subject must be {sorted(rule.subject_types)}, got {subject.type}")
    if obj is None:
        problems.append("object ID does not resolve in the catalog")
    elif obj.type not in rule.object_types:
        problems.append(f"{predicate} object must be {sorted(rule.object_types)}, got {obj.type}")
    elif rule.object_asset_kind and obj.type == "asset" and obj.properties.get("kind") != rule.object_asset_kind:
        problems.append(f"{predicate} requires an asset of kind {rule.object_asset_kind}")
    if rule.direction_required and (effect_direction in ("unknown", "not_applicable") or not outcome_text):
        problems.append(f"{predicate} requires an explicit direction and outcome")
    return problems
