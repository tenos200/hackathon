"""Mondo labels (OBO) and typed mappings (SSSOM TSV) for selected disease IDs.

Only explicit exact-equivalence mappings (`skos:exactMatch`) may support
identity or contribute HPO annotation rows. Broad, narrow and related mappings
stay typed links; `skos:closeMatch` is recorded as `related` because it is not
equivalence. Bare untyped cross-references are never used.
"""

from __future__ import annotations

from dataclasses import dataclass

from atlas.sources.hpo import normalize_disease_namespace

SSSOM_RELATIONS = {
    "skos:exactMatch": "exact",
    "skos:broadMatch": "broad",
    "skos:narrowMatch": "narrow",
    "skos:relatedMatch": "related",
    "skos:closeMatch": "related",
}
ANNOTATION_NAMESPACES = ("OMIM:", "ORPHA:", "DECIPHER:")


@dataclass(frozen=True)
class SssomRow:
    line_number: int
    subject_id: str
    predicate_id: str
    object_id: str          # namespace-normalized
    original_object_id: str
    mapping_justification: str | None

    @property
    def relation(self) -> str | None:
        return SSSOM_RELATIONS.get(self.predicate_id)


def parse_sssom(text: str) -> tuple[dict[str, str], list[SssomRow]]:
    metadata: dict[str, str] = {}
    header: list[str] | None = None
    rows: list[SssomRow] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        if line.startswith("#"):
            body = line[1:].strip()
            if ":" in body and not body.startswith("-") and not body.startswith(" "):
                key, _, value = body.partition(":")
                if value.strip():
                    metadata[key.strip()] = value.strip()
            continue
        cells = line.split("\t")
        if header is None:
            header = cells
            for required in ("subject_id", "predicate_id", "object_id"):
                if required not in header:
                    raise ValueError(f"SSSOM header lacks {required}")
            continue
        data = dict(zip(header, cells))
        rows.append(SssomRow(line_number=number, subject_id=data["subject_id"], predicate_id=data["predicate_id"],
                             object_id=normalize_disease_namespace(data["object_id"]),
                             original_object_id=data["object_id"],
                             mapping_justification=data.get("mapping_justification") or None))
    return metadata, rows


def exact_annotation_ids(mondo_id: str, rows: list[SssomRow]) -> list[SssomRow]:
    """Rows giving explicit exact equivalence to an HPO-annotation namespace."""
    return sorted(
        (r for r in rows if r.subject_id == mondo_id and r.relation == "exact"
         and r.object_id.startswith(ANNOTATION_NAMESPACES)),
        key=lambda r: r.object_id,
    )
