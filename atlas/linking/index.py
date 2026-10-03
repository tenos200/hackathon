"""Typed alias index and conservative entity linking (master plan section 4).

Order: exact identifier, then exact identity aliases (label, exact synonym,
alternate ID, approved symbol). An alias shared by several entities is
ambiguous and returns all candidates; it is never silently resolved.
Broad/narrow/related synonyms and secondary symbols only retrieve candidates
for contextual checking. Normalization for retrieval (Unicode NFKC, casefold,
whitespace collapse) keeps digits, Roman numerals and punctuation, so type
numbers stay distinct, and it never replaces the original identity.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from atlas.models.records import IDENTITY_ALIAS_TYPES, Entity
from atlas.sources.hpo import HpoOntology

_WS = re.compile(r"\s+")
_TOKEN = re.compile(r"[\w]+(?:[-'][\w]+)*", re.UNICODE)


def normalize_key(text: str) -> str:
    return _WS.sub(" ", unicodedata.normalize("NFKC", text).casefold()).strip()


@dataclass(frozen=True)
class AliasEntry:
    entity_id: str
    entity_type: str
    text: str
    alias_type: str
    source_document_id: str | None


@dataclass(frozen=True)
class LookupResult:
    mention: str
    resolved_id: str | None
    candidates: tuple[str, ...]
    ambiguous: bool
    via: str  # identifier | identity_alias | candidate_alias | none
    matched_alias: str | None


@dataclass
class AliasIndex:
    entries: dict[str, list[AliasEntry]] = field(default_factory=dict)
    ids: dict[str, str] = field(default_factory=dict)  # entity ID -> type

    def _add(self, entry: AliasEntry) -> None:
        self.entries.setdefault(normalize_key(entry.text), []).append(entry)

    def add_entity(self, entity: Entity) -> None:
        self.ids[entity.id] = entity.type
        self._add(AliasEntry(entity.id, entity.type, entity.label, "label", None))
        for alias in entity.aliases:
            if alias.text == entity.label and alias.alias_type == "label":
                continue
            self._add(AliasEntry(entity.id, entity.type, alias.text, alias.alias_type, alias.source_document_id))

    def add_hpo_ontology(self, ontology: HpoOntology, source_document_id: str | None) -> None:
        """Index labels, alternate IDs and typed synonyms across the entire pinned ontology."""
        scope_map = {"EXACT": "exact_synonym", "BROAD": "broad_synonym", "NARROW": "narrow_synonym",
                     "RELATED": "related_synonym"}
        for term in ontology.document.terms.values():
            if term.is_obsolete:
                continue
            self.ids[term.id] = "phenotype"
            if term.name:
                self._add(AliasEntry(term.id, "phenotype", term.name, "label", source_document_id))
            for alt in term.alt_ids:
                self.ids.setdefault(alt, "phenotype")
                self._add(AliasEntry(term.id, "phenotype", alt, "alt_id", source_document_id))
            for text, scope in term.synonyms:
                self._add(AliasEntry(term.id, "phenotype", text, scope_map[scope], source_document_id))

    def lookup(self, mention: str, entity_type: str | None = None) -> LookupResult:
        stripped = mention.strip()
        if stripped in self.ids and (entity_type is None or self.ids[stripped] == entity_type):
            primary = next((e.entity_id for e in self.entries.get(normalize_key(stripped), [])
                            if e.alias_type == "alt_id"), stripped)
            return LookupResult(mention, primary, (primary,), False, "identifier", stripped)
        hits = [e for e in self.entries.get(normalize_key(stripped), [])
                if entity_type is None or e.entity_type == entity_type]
        identity = sorted({e.entity_id for e in hits if e.alias_type in IDENTITY_ALIAS_TYPES})
        if len(identity) == 1:
            matched = next(e.text for e in hits if e.entity_id == identity[0] and e.alias_type in IDENTITY_ALIAS_TYPES)
            # Still ambiguous if another entity claims this text through any alias type.
            others = sorted({e.entity_id for e in hits} - set(identity))
            if not others:
                return LookupResult(mention, identity[0], tuple(identity), False, "identity_alias", matched)
            return LookupResult(mention, None, tuple(identity + others), True, "identity_alias", matched)
        if len(identity) > 1:
            return LookupResult(mention, None, tuple(identity), True, "identity_alias", stripped)
        candidates = sorted({e.entity_id for e in hits})
        if candidates:
            return LookupResult(mention, None, tuple(candidates), len(candidates) > 1, "candidate_alias", stripped)
        return LookupResult(mention, None, (), False, "none", None)

    def retrieve(self, mention: str, entity_type: str | None = None, limit: int = 5) -> list[str]:
        """Ordinary lexical retrieval of up to `limit` candidate IDs (no identity decision)."""
        query = set(_TOKEN.findall(normalize_key(mention)))
        if not query:
            return []
        scores: dict[str, float] = {}
        for key, entries in self.entries.items():
            tokens = set(_TOKEN.findall(key))
            if not tokens:
                continue
            overlap = len(query & tokens) / len(query | tokens)
            if overlap <= 0:
                continue
            for entry in entries:
                if entity_type is None or entry.entity_type == entity_type:
                    scores[entry.entity_id] = max(scores.get(entry.entity_id, 0.0), overlap)
        return [eid for eid, _ in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]]
