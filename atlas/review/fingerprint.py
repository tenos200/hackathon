"""Review fingerprints (master plan section 5.3).

A fingerprint is the content hash of a review target's substantive payload
plus the content hashes of its evidence, entity definitions and support
dependencies. It excludes review IDs, review states and operational timestamps,
which prevents circular hashes. `reviewed_content_sha256` in the public API is
this fingerprint at the time of the accepted review: the hash of the reviewed
internal record and its dependencies, not of the public DTO or of the private
review reasoning.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from atlas.hashing import content_sha256
from atlas.models.records import (
    Assertion, Conflict, Entity, Evidence, Explanation, Mapping, OpportunityRecord, ResearchContext, SourceDocument,
    Support,
)

REVIEW_FIELDS = ("review_state", "review_id")


def _without(payload: dict, *keys: str) -> dict:
    return {k: v for k, v in payload.items() if k not in keys}


def source_hash(document: SourceDocument) -> str:
    """Source identity and interpretation-relevant metadata; fetch time excluded."""
    return content_sha256(_without(document.payload(), "fetched_at", "canonical_text", "sections") |
                          {"sections": [s.payload() for s in document.sections]})


@dataclass
class Catalog:
    entities: dict[str, Entity] = field(default_factory=dict)
    contexts: dict[str, ResearchContext] = field(default_factory=dict)
    mappings: dict[str, Mapping] = field(default_factory=dict)
    sources: dict[str, SourceDocument] = field(default_factory=dict)
    evidence: dict[str, Evidence] = field(default_factory=dict)
    assertions: dict[str, Assertion] = field(default_factory=dict)
    supports: dict[str, Support] = field(default_factory=dict)
    conflicts: dict[str, Conflict] = field(default_factory=dict)
    opportunities: dict[str, OpportunityRecord] = field(default_factory=dict)
    explanations: dict[str, Explanation] = field(default_factory=dict)

    # -------------------------------------------------- dependency hashes

    def entity_hash(self, entity_id: str) -> str | None:
        entity = self.entities.get(entity_id)
        if entity is None:
            return None
        mappings = sorted(content_sha256(_without(m.payload(), "review_state")) for m in self.mappings.values()
                          if m.source_id == entity_id)
        return content_sha256({"entity": entity.payload(), "mappings": mappings})

    def context_hash(self, context_id: str | None) -> str | None:
        if context_id is None:
            return None
        context = self.contexts.get(context_id)
        if context is None:
            return None
        payload = _without(context.payload(), "definition_review_state")
        entity_ids = [context.disease_id, *(context.gene_ids), *([context.mechanism_id] if context.mechanism_id else [])]
        return content_sha256({"context": payload, "entities": {e: self.entity_hash(e) for e in sorted(entity_ids)}})

    def evidence_hash(self, evidence_id: str) -> str | None:
        evidence = self.evidence.get(evidence_id)
        if evidence is None:
            return None
        source = self.sources.get(evidence.source_document_id)
        return content_sha256({"evidence": evidence.payload(), "source": source_hash(source) if source else None})

    def assertion_core(self, assertion_id: str) -> str | None:
        """Substantive claim plus entity/context definitions (no supports)."""
        a = self.assertions.get(assertion_id)
        if a is None:
            return None
        return content_sha256({
            "assertion": _without(a.payload(), *REVIEW_FIELDS, "support_ids"),
            "subject": self.entity_hash(a.subject_id), "object": self.entity_hash(a.object_id),
            "context": self.context_hash(a.context_id),
        })

    # -------------------------------------------------- target fingerprints

    def fingerprint_assertion(self, assertion_id: str) -> str | None:
        a = self.assertions.get(assertion_id)
        if a is None:
            return None
        supports = []
        for sid in sorted(a.support_ids):
            support = self.supports.get(sid)
            if support is None:
                return None  # dangling support: cannot be reviewed
            supports.append({"support": _without(support.payload(), *REVIEW_FIELDS, "checker_result_id"),
                             "evidence": self.evidence_hash(support.evidence_id)})
        return content_sha256({"core": self.assertion_core(assertion_id), "supports": supports})

    def fingerprint_support(self, support_id: str) -> str | None:
        s = self.supports.get(support_id)
        if s is None:
            return None
        return content_sha256({"support": _without(s.payload(), *REVIEW_FIELDS, "checker_result_id"),
                               "evidence": self.evidence_hash(s.evidence_id),
                               "assertion": self.assertion_core(s.assertion_id)})

    def fingerprint_context(self, context_id: str) -> str | None:
        context = self.contexts.get(context_id)
        if context is None:
            return None
        return content_sha256({
            "context": self.context_hash(context_id),
            "definition_assertions": {a: self.fingerprint_assertion(a) for a in sorted(context.definition_assertion_ids)},
            "definition_evidence": {e: self.evidence_hash(e) for e in sorted(context.definition_evidence_ids)},
        })

    def fingerprint_mapping(self, mapping_id: str) -> str | None:
        m = self.mappings.get(mapping_id)
        if m is None:
            return None
        source = self.sources.get(m.source_document_id)
        return content_sha256({"mapping": _without(m.payload(), "review_state"),
                               "source": source_hash(source) if source else None})

    def fingerprint_entity(self, entity_id: str) -> str | None:
        return self.entity_hash(entity_id)

    def fingerprint_conflict(self, conflict_id: str) -> str | None:
        conflict = self.conflicts.get(conflict_id)
        if conflict is None:
            return None
        return content_sha256({"conflict": _without(conflict.payload(), *REVIEW_FIELDS),
                               "assertions": {a: self.fingerprint_assertion(a) for a in sorted(conflict.assertion_ids)},
                               "evidence": {e: self.evidence_hash(e) for e in sorted(conflict.evidence_ids)}})

    def fingerprint_opportunity(self, opportunity_id: str) -> str | None:
        """Comparison IDs are themselves content-derived (they embed calculation content hashes)."""
        opp = self.opportunities.get(opportunity_id)
        if opp is None:
            return None
        return content_sha256({
            "opportunity": _without(opp.payload(), *REVIEW_FIELDS),
            "asset": self.entity_hash(opp.asset_id),
            "partners": {p: self.entity_hash(p) for p in sorted(opp.partner_entity_ids)},
            "basis": {a: self.fingerprint_assertion(a) for a in sorted(opp.basis_assertion_ids)},
            "comparisons": sorted(opp.comparison_ids),
            "context": self.context_hash(opp.starting_context_id),
        })

    def fingerprint_explanation(self, explanation_id: str) -> str | None:
        explanation = self.explanations.get(explanation_id)
        if explanation is None:
            return None
        return content_sha256(_without(explanation.payload(), *REVIEW_FIELDS))

    def assertion_dependencies(self, assertion_id: str) -> set[str]:
        """Catalog IDs (entities, contexts, mappings) an assertion depends on, for impact reports."""
        a = self.assertions[assertion_id]
        deps = {a.subject_id, a.object_id}
        if a.context_id:
            deps.add(a.context_id)
            context = self.contexts.get(a.context_id)
            if context:
                deps.update({context.disease_id, *context.gene_ids, *([context.mechanism_id] if context.mechanism_id else [])})
        deps.update(m.id for m in self.mappings.values() if m.source_id in deps)
        return deps
