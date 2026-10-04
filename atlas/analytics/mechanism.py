"""Mechanism comparison cards (master plan section 9.2).

Each dimension is reported separately as same/different/unknown/not_comparable
with the accepted assertions behind it. There is no combined mechanism score.
A matching functional-effect label is a descriptive match, never mechanistic
equivalence. Free-text cell/tissue and assay/species contexts are not
compared semantically by code: identical recorded text is `same`, otherwise
`not_comparable` (expert comparison needed); absent data stays `unknown`.
"""

from __future__ import annotations

from dataclasses import dataclass

from atlas.models.records import Entity, MechanismFeatureRecord


@dataclass(frozen=True)
class MechanismView:
    """Mechanism facts usable for comparison: only from accepted definitions/assertions."""

    context_id: str
    mechanism: Entity | None
    definition_assertion_ids: tuple[str, ...]
    process_assertions: dict[str, tuple[str, ...]]  # process ID -> accepted mechanism_affects_process assertion IDs


def _text_dimension(name: str, a: str | None, b: str | None, ids: list[str]) -> MechanismFeatureRecord:
    if a is None or b is None:
        return MechanismFeatureRecord(dimension=name, comparison="unknown", assertion_ids=ids,
                                      explanation=f"{name.replace('_', ' ').capitalize()} is not recorded for both contexts.")
    if a == b:
        return MechanismFeatureRecord(dimension=name, comparison="same", assertion_ids=ids,
                                      explanation=f"Both definitions record the same text: “{a}”. "
                                                  "This is descriptive and does not establish equivalent biology.")
    return MechanismFeatureRecord(dimension=name, comparison="not_comparable", assertion_ids=ids,
                                  explanation=f"Recorded as “{a}” versus “{b}”; comparing these needs expert judgment.")


def compare_mechanisms(a: MechanismView, b: MechanismView) -> list[MechanismFeatureRecord]:
    ids = sorted({*a.definition_assertion_ids, *b.definition_assertion_ids})
    if a.mechanism is None or b.mechanism is None:
        missing = "both contexts" if a.mechanism is None and b.mechanism is None else (
            "the first context" if a.mechanism is None else "the second context")
        return [MechanismFeatureRecord(
            dimension="functional_effect", comparison="unknown", assertion_ids=ids,
            explanation=f"No accepted mechanism definition for {missing}; mechanism is unknown, not absent.")]
    pa, pb = a.mechanism.properties, b.mechanism.properties
    features: list[MechanismFeatureRecord] = []
    ea, eb = pa["functional_effect"], pb["functional_effect"]
    if "unknown" in (ea, eb):
        features.append(MechanismFeatureRecord(dimension="functional_effect", comparison="unknown", assertion_ids=ids,
                                               explanation="At least one functional effect is recorded as unknown."))
    elif ea == eb:
        features.append(MechanismFeatureRecord(
            dimension="functional_effect", comparison="same", assertion_ids=ids,
            explanation=f"Both are recorded as {ea.replace('_', '-')} effects; a shared label does not establish "
                        "equivalent biology."))
    else:
        features.append(MechanismFeatureRecord(
            dimension="functional_effect", comparison="different", assertion_ids=ids,
            explanation=f"Recorded effects differ ({ea.replace('_', '-')} versus {eb.replace('_', '-')})."))
    if pa["gene_id"] == pb["gene_id"]:
        features.append(MechanismFeatureRecord(dimension="gene", comparison="same", assertion_ids=ids,
                                               explanation="Both mechanism definitions concern the same gene."))
    else:
        features.append(MechanismFeatureRecord(dimension="gene", comparison="different", assertion_ids=ids,
                                               explanation="The mechanism definitions concern different genes; "
                                                           "same effect across genes is never an identity merge."))
    procs_a, procs_b = set(a.process_assertions), set(b.process_assertions)
    proc_ids = sorted({x for v in (*a.process_assertions.values(), *b.process_assertions.values()) for x in v})
    if not procs_a or not procs_b:
        features.append(MechanismFeatureRecord(dimension="affected_process", comparison="unknown",
                                               assertion_ids=proc_ids or ids,
                                               explanation="Affected processes are not evidenced for both contexts."))
    elif procs_a == procs_b:
        features.append(MechanismFeatureRecord(dimension="affected_process", comparison="same", assertion_ids=proc_ids,
                                               explanation="The same affected processes are evidenced; cell and "
                                                           "assay context still differ or are unassessed."))
    else:
        features.append(MechanismFeatureRecord(
            dimension="affected_process", comparison="not_comparable", assertion_ids=proc_ids,
            explanation=f"Evidenced processes differ ({', '.join(sorted(procs_a))} versus {', '.join(sorted(procs_b))}); "
                        "overlap may raise a question but does not erase context differences."))
    features.append(_text_dimension("cell_or_tissue_context", pa.get("cell_tissue_text"), pb.get("cell_tissue_text"), ids))
    features.append(_text_dimension("assay_or_species_context", pa.get("assay_species_text"), pb.get("assay_species_text"), ids))
    return features
