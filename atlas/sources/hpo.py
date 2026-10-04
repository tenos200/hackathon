"""HPO ontology structure, phenotype.hpoa annotations and the OMIM IC reference.

Annotation semantics follow the HPO annotation format: the `qualifier` column
is empty or `NOT`; NOT rows are negative evidence and never enter positive
similarity sets; frequency, onset, reference and evidence code are retained.
Only aspect `P` (phenotypic abnormality) rows participate in phenotype
matching.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from atlas.sources.obo import OboDocument, parse_obo

HPO_ROOT = "HP:0000001"
PHENOTYPIC_ABNORMALITY = "HP:0000118"
ROOT_TERMS = frozenset({HPO_ROOT, PHENOTYPIC_ABNORMALITY})


def normalize_disease_namespace(identifier: str) -> str:
    """Normalize namespace spellings only (never disease granularity)."""
    prefix, sep, local = identifier.partition(":")
    if not sep:
        return identifier
    upper = prefix.upper()
    if upper in ("OMIM", "MIM"):
        return f"OMIM:{local}"
    if upper in ("ORPHA", "ORPHANET", "ORDO"):
        return f"ORPHA:{local}"
    if upper == "DECIPHER":
        return f"DECIPHER:{local}"
    return identifier


class OntologyCycleError(ValueError):
    pass


@dataclass
class HpoOntology:
    document: OboDocument
    source_sha256: str
    _primary: dict[str, str] = field(default_factory=dict)
    _ancestors: dict[str, frozenset[str]] = field(default_factory=dict)

    @classmethod
    def from_obo(cls, text: str, source_sha256: str) -> "HpoOntology":
        onto = cls(parse_obo(text), source_sha256)
        for term in onto.document.terms.values():
            onto._primary[term.id] = term.id
            for alt in term.alt_ids:
                onto._primary.setdefault(alt, term.id)
        onto.check_acyclic()
        return onto

    @classmethod
    def from_document(cls, document: OboDocument, source_sha256: str) -> "HpoOntology":
        onto = cls(document, source_sha256)
        for term in document.terms.values():
            onto._primary[term.id] = term.id
            for alt in term.alt_ids:
                onto._primary.setdefault(alt, term.id)
        onto.check_acyclic()
        return onto

    @property
    def release(self) -> str | None:
        return self.document.data_version

    def label(self, term_id: str) -> str | None:
        term = self.document.terms.get(term_id)
        return term.name if term else None

    def resolve(self, term_id: str) -> str | None:
        """Primary current ID, via alt_id or an unambiguous source-provided replacement only."""
        primary = self._primary.get(term_id)
        if primary is None:
            return None
        term = self.document.terms[primary]
        if not term.is_obsolete:
            return primary
        if len(term.replaced_by) == 1 and term.replaced_by[0] in self.document.terms:
            replacement = self.document.terms[term.replaced_by[0]]
            return replacement.id if not replacement.is_obsolete else None
        return None

    def check_acyclic(self) -> None:
        """Three-colour DFS over the whole is_a graph; raises on any cycle."""
        WHITE, GREY, BLACK = 0, 1, 2
        colour = {tid: WHITE for tid in self.document.terms}
        for root in self.document.terms:
            if colour[root] != WHITE:
                continue
            stack: list[tuple[str, int]] = [(root, 0)]
            colour[root] = GREY
            while stack:
                node, index = stack[-1]
                parents = self.document.terms[node].is_a
                if index < len(parents):
                    stack[-1] = (node, index + 1)
                    parent = parents[index]
                    if parent not in colour:
                        continue  # dangling reference: no ancestors beyond it
                    if colour[parent] == GREY:
                        raise OntologyCycleError(f"is_a cycle through {parent}")
                    if colour[parent] == WHITE:
                        colour[parent] = GREY
                        stack.append((parent, 0))
                else:
                    colour[node] = BLACK
                    stack.pop()

    def ancestors(self, term_id: str) -> frozenset[str]:
        """The term itself plus all is_a ancestors; multiple parents propagate once."""
        cached = self._ancestors.get(term_id)
        if cached is not None:
            return cached
        seen: set[str] = {term_id}
        frontier = [term_id]
        while frontier:
            term = self.document.terms.get(frontier.pop())
            for parent in (term.is_a if term else []):
                if parent not in seen:
                    seen.add(parent)
                    frontier.append(parent)
        frozen = frozenset(seen)
        self._ancestors[term_id] = frozen
        return frozen

    def is_phenotypic(self, term_id: str) -> bool:
        return PHENOTYPIC_ABNORMALITY in self.ancestors(term_id)


def load_ontology(text: str, sha256: str) -> HpoOntology:
    return HpoOntology.from_obo(text, sha256)


# ---------------------------------------------------------------- annotations

_COLUMN_KEYS = {
    "databaseid": "database_id", "diseasename": "disease_name", "qualifier": "qualifier", "hpoid": "hpo_id",
    "reference": "reference", "evidence": "evidence", "onset": "onset", "frequency": "frequency", "sex": "sex",
    "modifier": "modifier", "aspect": "aspect", "biocuration": "biocuration",
}


@dataclass(frozen=True)
class HpoaRow:
    line_number: int
    database_id: str      # normalized namespace
    original_database_id: str
    disease_name: str
    qualifier: str        # "" or "NOT"
    hpo_id: str
    reference: str
    evidence: str
    onset: str
    frequency: str
    sex: str
    modifier: str
    aspect: str
    biocuration: str

    @property
    def negated(self) -> bool:
        return self.qualifier.upper() == "NOT"

    def fields(self) -> list[tuple[str, str]]:
        return [("database_id", self.original_database_id), ("disease_name", self.disease_name),
                ("qualifier", self.qualifier), ("hpo_id", self.hpo_id), ("reference", self.reference),
                ("evidence", self.evidence), ("onset", self.onset), ("frequency", self.frequency),
                ("sex", self.sex), ("modifier", self.modifier), ("aspect", self.aspect),
                ("biocuration", self.biocuration)]


@dataclass
class HpoaFile:
    metadata: dict[str, str]
    rows: list[HpoaRow]
    source_sha256: str

    @property
    def release(self) -> str | None:
        return self.metadata.get("version") or self.metadata.get("date")


def parse_hpoa(text: str, source_sha256: str) -> HpoaFile:
    metadata: dict[str, str] = {}
    header: list[str] | None = None
    rows: list[HpoaRow] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        if line.startswith("#") and header is None:
            body = line.lstrip("#").strip()
            if ":" in body and "\t" not in body:
                key, _, value = body.partition(":")
                metadata[key.strip().lower()] = value.strip()
                continue
            if "\t" in body:
                header = [_COLUMN_KEYS.get(c.strip().lower().replace("_", ""), c) for c in body.split("\t")]
            continue
        cells = line.split("\t")
        if header is None:
            header = [_COLUMN_KEYS.get(c.strip().lower().replace("_", ""), c) for c in cells]
            continue
        if len(cells) != len(header):
            raise ValueError(f"phenotype.hpoa line {number}: expected {len(header)} columns, got {len(cells)}")
        data = dict(zip(header, cells))
        missing = [k for k in _COLUMN_KEYS.values() if k not in data]
        if missing:
            raise ValueError(f"phenotype.hpoa header lacks columns {missing}")
        rows.append(HpoaRow(line_number=number, database_id=normalize_disease_namespace(data["database_id"]),
                            original_database_id=data["database_id"],
                            **{k: data[k] for k in _COLUMN_KEYS.values() if k != "database_id"}))
    return HpoaFile(metadata=metadata, rows=rows, source_sha256=source_sha256)


# ---------------------------------------------------------------- IC reference


@dataclass(frozen=True)
class IcReference:
    """Fixed OMIM-only reference population: distinct OMIM IDs with positive aspect-P annotations."""

    release: str | None
    source_sha256: str
    disease_count: int
    counts: dict[str, int]

    def ic(self, term_id: str) -> float | None:
        count = self.counts.get(term_id, 0)
        if count <= 0 or self.disease_count <= 0:
            return None  # unknown reference support, never infinite IC
        return -math.log(count / self.disease_count)


def build_ic_reference(annotations: HpoaFile, ontology: HpoOntology) -> IcReference:
    per_disease: dict[str, set[str]] = {}
    for row in annotations.rows:
        if row.negated or row.aspect != "P" or not row.database_id.startswith("OMIM:"):
            continue
        term = ontology.resolve(row.hpo_id)
        if term is None:
            continue
        per_disease.setdefault(row.database_id, set()).update(ontology.ancestors(term))
    counts: dict[str, int] = {}
    for terms in per_disease.values():
        for term in terms:
            counts[term] = counts.get(term, 0) + 1
    return IcReference(release=annotations.release, source_sha256=annotations.source_sha256,
                       disease_count=len(per_disease), counts=counts)
