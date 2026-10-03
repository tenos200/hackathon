"""Real-mode read model: deterministic projection of one pinned, validated package.

Loading (startup only): read the pinned snapshot through the read-only role (or
a local package file for the documented local fallback), recompute its content
hash, check every reference, then project and schema-validate every route
payload against the committed contract. Only then is the service ready.
Requests are answered from this in-memory projection; there are no writes,
downloads, model calls or generated prose. Template sentences describe one
record or one calculation each; multi-record synthesis comes only from
reviewed explanation records.
"""

from __future__ import annotations

import json
from pathlib import Path

from atlas.api import dto
from atlas.api.settings import Settings
from atlas.api.store import MAX_COMPARISONS, MAX_GRAPH_LINKS, MAX_GRAPH_NODES, NotFound, bound_search
from atlas.assembly.package import PackageContent, PublicEvidence, ProfileSnapshot, load_package
from atlas.linking.index import normalize_key
from atlas.models.records import ComparisonRecord, PhenotypeResult, SentenceRecord

PREDICATE_PHRASES = {
    "gene_associated_with_disease": "is recorded as associated with", "variant_in_gene": "is located in",
    "variant_associated_with_disease": "is recorded as associated with", "variant_has_effect": "has the recorded effect",
    "disease_has_mechanism": "has the recorded mechanism", "mechanism_causes_disease": "is reported to cause",
    "has_phenotype": "has the recorded phenotype", "gene_involved_in_process": "is annotated as involved in",
    "mechanism_affects_process": "is reported to affect", "responds_to": "is reported in relation to",
    "studies": "is a registered study of", "tests": "registers the testing of", "serves": "serves the community for",
    "owns_or_runs": "owns or runs", "asset_for_context": "is intended for", "professional_at": "is affiliated with",
    "works_on": "works on",
    "mentions": "names in its text (no relationship asserted)", "investigator_on": "is listed as an investigator on",
    "funds": "is the recorded funder of",
}
STATUS_PHRASES = {
    "reported_result": "reported result", "proposed": "proposed", "planned": "planned work", "ongoing": "ongoing work",
    "background": "background statement", "negated": "explicitly negated", "inconclusive": "inconclusive",
    "listed_record": "listed database record",
}
ACCESS = {"explicit": "available_with_terms", "restricted": "contact_owner", "unknown": "unknown"}
STANDARD_LIMITATIONS = [
    "Research navigation only: no diagnosis, treatment recommendation, eligibility or individual mechanism inference.",
    "Accepted means a faithful sourced statement after review, not proof of biology.",
    "Neighborhoods are exploratory clustering through explainable similarity neighborhoods, not validated disease classes.",
    "Coverage describes the executed queries and inspected sources, not all existing knowledge.",
]


def _sentence(s: SentenceRecord) -> dto.Sentence:
    return dto.Sentence(text=s.text, assertion_ids=s.assertion_ids, calculation_ids=s.calculation_ids,
                        opportunity_ids=s.opportunity_ids)


def _score(r: PhenotypeResult) -> dto.Score:
    cov = {k: dto.Coverage(direct_terms=v.direct_terms, assessed_terms=v.assessed_terms, fraction=v.fraction,
                           unassessed_term_ids=v.unassessed_term_ids) for k, v in r.reference_coverage.items()}
    return dto.Score(
        mapped_annotation_ids=dto.PairIds(**r.mapped_annotation_ids), profile_level=r.profile_level, score=r.score,
        calculation_id=r.calculation_id, same_parent_disease=r.same_parent_disease,
        direct_term_counts=dto.PairCounts(**r.direct_annotation_counts),
        publication_counts=dto.PairCounts(**r.publication_counts), reference_coverage=dto.PairCoverage(**cov),
        input_assertion_ids=dto.PairIds(**r.input_assertion_ids),
        shared_terms=[dto.SharedTerm(id=t.id, label=t.label, ic=t.ic, score_contribution=t.score_contribution,
                                     direct_a=t.direct_a, direct_b=t.direct_b, input_assertion_ids=t.input_assertion_ids)
                      for t in r.shared_terms],
        specific_shared_term_ids=r.specific_shared_terms, missingness=r.missingness,
        warnings=[dto.Warning(**w) for w in r.warnings])


def _profile(p: ProfileSnapshot) -> dto.Profile:
    return dto.Profile(mapped_annotation_ids=p.mapped_annotation_ids,
                       terms=[dto.ProfileTerm(id=t.id, label=t.label, assertion_ids=t.assertion_ids) for t in p.terms],
                       direct_term_count=p.direct_term_count, distinct_publication_count=p.distinct_publication_count,
                       reference_assessed_count=p.reference_assessed_count, unassessed_term_ids=p.unassessed_term_ids)


def card_order(card: ComparisonRecord) -> tuple:
    """Ranked cards by score (descending) then context ID; then unranked cards in stable ID order."""
    if card.ranking_basis == "unranked":
        return (1, 0.0, card.context_b)
    result = card.subgroup_comparison if card.ranking_basis == "subgroup" else card.disease_baseline
    return (0, -(result.score or 0.0), card.context_b)


class RealStore:
    data_mode = "real"

    def __init__(self, snapshot_id: str, content: PackageContent) -> None:
        self.snapshot_id = snapshot_id
        self.c = content
        self.entities = {e.id: e for e in content.entities}
        self.contexts = {c.record.id: c for c in content.contexts}
        self.sources = {s.id: s for s in content.sources}
        self.evidence = {e.id: e for e in content.evidence}
        self.assertions = {a.record.id: a for a in content.assertions}
        self.supports = {s.id: s for s in content.supports}
        self.calculations = {c.id: c for c in content.calculations}
        self.conflicts = content.conflicts
        self._ext = None
        self.explanations = {}
        for x in content.explanations:
            self.explanations.setdefault(x.context_id, []).append(x)

    @property
    def ext(self):
        if self._ext is None:
            from atlas.api.ext_store import ExtensionProjection

            self._ext = ExtensionProjection(self)
        return self._ext

    # ------------------------------------------------------------ helpers

    def _envelope(self, model, data, warnings=None):
        return model(contract_version=dto.CONTRACT_VERSION, snapshot_id=self.snapshot_id, data_mode="real",
                     data=data, warnings=warnings or [])

    def _ref(self, entity_id: str) -> dto.EntityRef:
        entity = self.entities.get(entity_id)
        return dto.EntityRef(id=entity_id, label=entity.label if entity else entity_id)

    def _option(self, context_id: str) -> dto.ContextOption:
        ctx = self.contexts[context_id].record
        return dto.ContextOption(id=ctx.id, label=ctx.label, profile_level=ctx.profile_level,
                                 mechanism_known=ctx.mechanism_id is not None)

    def _context_ref(self, context_id: str) -> dto.EntityRef:
        return dto.EntityRef(id=context_id, label=self.contexts[context_id].record.label)

    @staticmethod
    def scope_text(scope) -> str:
        parts = [f"{name.replace('_text', '').replace('_', ' ')}: {value}" for name, value in scope.text_qualifiers()]
        if scope.variant_ids:
            parts.append("variants: " + ", ".join(scope.variant_ids))
        return "; ".join(parts) if parts else "No additional scope qualifiers recorded."

    def record_sentence(self, assertion_id: str) -> dto.Sentence:
        """Template describing exactly one published record with its status and scope."""
        a = self.assertions[assertion_id].record
        text = (f"{self._ref(a.subject_id).label} {PREDICATE_PHRASES[a.predicate]} {self._ref(a.object_id).label} "
                f"({STATUS_PHRASES[a.statement_status]}; {self.scope_text(a.scope)})")
        return dto.Sentence(text=text, assertion_ids=[a.id], calculation_ids=[], opportunity_ids=[])

    # ------------------------------------------------------------ routes

    def meta(self) -> dto.MetaResponse:
        r = self.c.release
        return self._envelope(dto.MetaResponse, dto.MetaData(
            published_at=r.published_at, content_sha256=self.snapshot_id.removeprefix("snap_"),
            source_versions=[dto.SourceVersion(name=n, version=v) for n, v in r.source_versions],
            counts=dto.Counts(documents=r.corpus_counts["documents"],
                              published_assertions=r.corpus_counts["published_assertions"],
                              contexts=r.corpus_counts["contexts"]),
            algorithm_version=r.algorithm_version,
            capabilities=dto.Capabilities(live_ai=False, request_snapshot_pin=False, graph=True),
            example_contexts=[self._option(c) for c in r.example_context_ids],
            limitations=[*r.limitations, *STANDARD_LIMITATIONS]))

    def _contexts_for(self, entity_id: str) -> list[str]:
        entity = self.entities[entity_id]
        found: set[str] = set()
        for cid, pc in self.contexts.items():
            ctx = pc.record
            if entity.type == "gene" and entity_id in ctx.gene_ids:
                found.add(cid)
            elif entity.type == "disease" and ctx.disease_id == entity_id:
                found.add(cid)
            elif entity.type == "mechanism" and ctx.mechanism_id == entity_id:
                found.add(cid)
            elif entity.type == "phenotype" and any(
                    t.id == entity_id for p in (pc.disease_profile, pc.subgroup_profile) if p for t in p.terms):
                found.add(cid)
        for a in self.assertions.values():
            rec = a.record
            if entity.type == "organization" and rec.predicate == "serves" and rec.subject_id == entity_id:
                found |= {cid for cid, pc in self.contexts.items() if pc.record.disease_id == rec.object_id}
            if entity.type == "asset" and rec.predicate == "asset_for_context" and rec.subject_id == entity_id \
                    and rec.context_id:
                found.add(rec.context_id)
        return sorted(found)

    def search(self, query: str) -> dto.SearchResponse:
        key = normalize_key(query)
        alias_owners: dict[str, set[str]] = {}
        for e in self.entities.values():
            for text in [e.label, *(a.text for a in e.aliases)]:
                alias_owners.setdefault(normalize_key(text), set()).add(e.id)
        ranked: list[tuple[int, str, str, str | None]] = []
        for e in self.entities.values():
            best: tuple[int, str | None] | None = None
            for text in [e.label, *(a.text for a in e.aliases), e.id]:
                k = normalize_key(text)
                rank = 0 if k == key else 1 if k.startswith(key) else 2 if key in k else None
                if rank is not None and (best is None or rank < best[0]):
                    best = (rank, text)
            if best is not None:
                ranked.append((best[0], e.label.casefold(), e.id, best[1]))
        ranked.sort()
        matches = []
        for rank, _, entity_id, alias in ranked:
            entity = self.entities[entity_id]
            owners = alias_owners.get(normalize_key(alias or ""), set())
            exact_hits = sum(1 for r in ranked if r[0] == 0)
            ambiguous = len(owners) > 1 or (rank == 0 and exact_hits > 1)
            matches.append(dto.SearchMatch(
                id=entity_id, entity_type=entity.type, label=entity.label,
                matched_alias=alias if alias != entity.label else None, ambiguous=ambiguous,
                contexts=[self._option(c) for c in self._contexts_for(entity_id)]))
        bounded, warnings = bound_search(matches)
        return self._envelope(dto.SearchResponse, dto.SearchData(query=query, matches=bounded), warnings)

    def context(self, context_id: str) -> dto.ContextResponse:
        pc = self.contexts.get(context_id)
        if pc is None:
            raise NotFound(context_id)
        ctx = pc.record
        mechanism = None
        if ctx.mechanism_id:
            m = self.entities[ctx.mechanism_id]
            mechanism = dto.Mechanism(id=m.id, label=m.label, effect=m.properties["functional_effect"])
        reviewed = self.explanations.get(context_id, [])
        if reviewed:
            summary = [_sentence(s) for x in reviewed for s in x.sentences]
        else:
            summary = [self.record_sentence(a) for a in ctx.definition_assertion_ids if a in self.assertions]
        limitations = ["A research context is a browsing grouping, not a diagnosis or a new disease concept."]
        if ctx.mechanism_id is None:
            limitations.append("Mechanism is unknown for this context; nothing is inferred for an individual.")
        if not pc.audit.get("baseline_available"):
            limitations.append("No exact annotated disease mapping was found in the audit; the disease baseline is unavailable.")
        if pc.disease_profile.unassessed_term_ids:
            limitations.append("Some direct baseline terms lack IC-reference support; numerical scores are withheld.")
        if pc.subgroup_profile is not None and pc.subgroup_profile.direct_term_count == 0:
            limitations.append("No accepted scoped phenotype evidence for this subgroup; its profile is missing, not empty.")
        return self._envelope(dto.ContextResponse, dto.ContextData(
            id=ctx.id, label=ctx.label, profile_level=ctx.profile_level, gene_ids=ctx.gene_ids,
            disease=self._ref(ctx.disease_id), mechanism=mechanism, scope_text=ctx.scope,
            definition_assertion_ids=ctx.definition_assertion_ids, summary=summary,
            disease_profile=_profile(pc.disease_profile),
            subgroup_profile=_profile(pc.subgroup_profile) if pc.subgroup_profile else None,
            limitations=limitations))

    def _comparison(self, c: ComparisonRecord) -> dto.Comparison:
        return dto.Comparison(
            id=c.id, context_a=self._context_ref(c.context_a), context_b=self._context_ref(c.context_b),
            disease_baseline=_score(c.disease_baseline), subgroup_comparison=_score(c.subgroup_comparison),
            ranking_basis=c.ranking_basis,
            mechanism_features=[dto.MechanismFeature(dimension=f.dimension, comparison=f.comparison,
                                                     assertion_ids=f.assertion_ids, explanation=f.explanation)
                                for f in c.mechanism_features],
            relevant_differences=c.relevant_differences, unknowns=c.unknowns,
            summary=[_sentence(s) for s in c.summary])

    def connections(self, context_id: str) -> dto.ConnectionsResponse:
        from atlas.analytics.neighborhood import counterexamples

        if context_id not in self.contexts:
            raise NotFound(context_id)
        cards = sorted((c for c in self.c.comparisons if c.context_a == context_id), key=card_order)[:MAX_COMPARISONS]
        counter: list[dto.Sentence] = []
        for card in cards:
            for s in counterexamples(card):
                sentence = _sentence(s)
                if sentence not in counter:
                    counter.append(sentence)
        nodes = [dto.GraphNode(id=context_id, label=self.contexts[context_id].record.label, type="context")]
        links = []
        for card in cards:
            nodes.append(dto.GraphNode(id=card.context_b, label=self.contexts[card.context_b].record.label, type="context"))
            calc = (card.subgroup_comparison if card.ranking_basis == "subgroup" else card.disease_baseline).calculation_id
            links.append(dto.GraphLink(id=f"link:{card.id}", source=context_id, target=card.context_b,
                                       assertion_id=None, calculation_id=calc, computed=True))
        graph = dto.Graph(nodes=nodes[:MAX_GRAPH_NODES], links=links[:MAX_GRAPH_LINKS]) if cards else None
        return self._envelope(dto.ConnectionsResponse, dto.ConnectionsData(
            context_id=context_id, comparisons=[self._comparison(c) for c in cards], counterexamples=counter,
            graph=graph))

    def _source(self, source_id: str) -> dto.Source:
        s = self.sources[source_id]
        return dto.Source(
            id=s.id, title=s.title, url=s.url, external_ref=s.external_ref, source_kind=s.source_kind,
            release_or_version=s.release_or_version, publication_status=s.publication_status,
            canonical_sha256=s.canonical_sha256, raw_sha256=s.raw_sha256, canonicalizer_version=s.canonicalizer_version,
            public_text_policy=s.public_text_policy, metadata=dto.SourceMetadata(**s.metadata))

    def _evidence_views(self, ev: PublicEvidence, stance: str) -> list[dto.EvidenceView]:
        source = self.sources[ev.source_id]
        scope_bits = [f"study design: {ev.study_design}", f"species: {ev.species or 'not stated'}"]
        if ev.patient_count is not None:
            scope_bits.append(f"patients: {ev.patient_count}")
        if ev.study_or_cohort_ids:
            scope_bits.append("cohorts: " + ", ".join(ev.study_or_cohort_ids))
        common = dict(stance=stance, kind=ev.kind, source=self._source(ev.source_id), record_locator=ev.record_locator,
                      study_or_cohort_ids=ev.study_or_cohort_ids, independence=ev.independence,
                      record_fields=[dto.RecordField(name=n, value=v) for n, v in ev.record_fields],
                      study_design=ev.study_design, species=ev.species, patient_count=ev.patient_count,
                      source_native_validity=ev.source_native_validity, scope_text="; ".join(scope_bits))
        if ev.kind != "text_spans" or not ev.spans:
            return [dto.EvidenceView(id=ev.id, context=None, **common)]
        grouped: dict[tuple[int, int], list] = {}
        for span in ev.spans:
            window = next(w for w in source.context_windows if w.start <= span.start and span.end <= w.end)
            grouped.setdefault((window.start, window.end), []).append(span)
        views = []
        for index, ((start, end), spans) in enumerate(sorted(grouped.items())):
            window = next(w for w in source.context_windows if (w.start, w.end) == (start, end))
            context = dto.TextContext(text=window.text, canonical_start=start, canonical_end=end, highlights=[
                dto.Highlight(start=s.start - start, end=s.end - start, quote=s.quote) for s in spans])
            view_id = ev.id if len(grouped) == 1 else f"{ev.id}#window{index + 1}"
            views.append(dto.EvidenceView(id=view_id, context=context, **common))
        return views

    def assertion(self, assertion_id: str) -> dto.AssertionResponse:
        pa = self.assertions.get(assertion_id)
        if pa is None:
            raise NotFound(assertion_id)
        a = pa.record
        evidence: list[dto.EvidenceView] = []
        stances = set()
        for sid in a.support_ids:
            support = self.supports[sid]
            stances.add(support.stance)
            evidence += self._evidence_views(self.evidence[support.evidence_id], support.stance)
        conflicts = [dto.ConflictView(id=c.id, comparability=c.comparability, assertion_ids=c.assertion_ids,
                                      description=c.reason) for c in self.conflicts if a.id in c.assertion_ids]
        badges = [dto.Badge(kind="origin", label=a.origin.replace("_", " ")),
                  dto.Badge(kind="statement_status", label=STATUS_PHRASES[a.statement_status]),
                  dto.Badge(kind="review", label="Expert-reviewed" if pa.review.expert_validation
                            else "Source-fidelity reviewed (not expert validation)")]
        for kind in sorted({self.sources[self.evidence[self.supports[s].evidence_id].source_id].source_kind
                            for s in a.support_ids}):
            badges.append(dto.Badge(kind="source_type", label=kind.replace("_", " ")))
        for ev in sorted({self.supports[s].evidence_id for s in a.support_ids}):
            e = self.evidence[ev]
            if e.source_native_validity:
                badges.append(dto.Badge(kind="native_validity", label=f"Source classification: {e.source_native_validity}"))
            if e.publication_status == "preprint":
                badges.append(dto.Badge(kind="publication_status", label="Preprint"))
            if e.species:
                badges.append(dto.Badge(kind="species", label=e.species))
        if "opposes" in stances or conflicts:
            label = ("Results in different contexts" if conflicts and all(c.comparability == "different_context"
                                                                         for c in conflicts) else "Conflicting accounts")
            badges.append(dto.Badge(kind="conflict", label=label))
        unique_badges = list(dict.fromkeys(badges))
        return self._envelope(dto.AssertionResponse, dto.AssertionData(
            id=a.id, subject=self._ref(a.subject_id), predicate=a.predicate, object=self._ref(a.object_id),
            context_id=a.context_id, scope_text=self.scope_text(a.scope), statement_status=a.statement_status,
            effect_direction=a.effect_direction, origin=a.origin,
            review=dto.ReviewSummary(status="accepted", description=pa.review.description,
                                     expert_validation=pa.review.expert_validation,
                                     reviewed_content_sha256=pa.review.reviewed_content_sha256),
            badges=unique_badges, evidence=evidence, conflicts=conflicts))

    def calculation(self, calculation_id: str) -> dto.CalculationResponse:
        calc = self.calculations.get(calculation_id)
        if calc is None:
            raise NotFound(calculation_id)
        r = calc.result
        outcome = (f"result {r.score:.4f}" if r.score is not None else
                   "no numerical result: " + (r.missingness[0] if r.missingness else "unavailable"))
        explanation = [dto.Sentence(
            text=(f"Weighted Jaccard overlap of ancestor-closed HPO term sets at the "
                  f"{r.profile_level.replace('_', ' ')} level, weighted by OMIM-reference information content; "
                  f"{outcome}."), assertion_ids=[], calculation_ids=[calc.id], opportunity_ids=[])]
        return self._envelope(dto.CalculationResponse, dto.CalculationData(
            id=calc.id, kind=calc.kind, algorithm_version=calc.algorithm_version,
            parameters=[dto.Parameter(name=k, value=v) for k, v in sorted(calc.parameters.items())],
            input_assertion_ids=calc.input_assertion_ids, input_evidence_ids=calc.input_evidence_ids,
            reference_sources=[self._source(s) for s in calc.reference_source_ids],
            result=_score(r), explanation=explanation,
            limitations=["A browsing heuristic in [0,1]; not a probability, causal distance or clinical match.",
                         "Unrecorded phenotypes are not evidence of absence; ancestors are not extra observations.",
                         "Disease-baseline and subgroup results are separate and never substitute for each other."]))

    def _asset(self, asset_id: str) -> dto.Asset:
        entity = self.entities[asset_id]
        props = entity.properties
        owner, sentences, assertion_ids = None, [], []
        for pa in sorted(self.assertions.values(), key=lambda x: x.record.id):
            a = pa.record
            if a.predicate == "owns_or_runs" and a.object_id == asset_id:
                owner = self._ref(a.subject_id)
                sentences.append(self.record_sentence(a.id))
                assertion_ids.append(a.id)
            elif a.predicate == "asset_for_context" and a.subject_id == asset_id:
                sentences.append(self.record_sentence(a.id))
                assertion_ids.append(a.id)
        return dto.Asset(id=asset_id, name=entity.label, kind=props["kind"], owner=owner, url=props.get("url"),
                         description=sentences, assertion_ids=sorted(set(assertion_ids)),
                         access_status=ACCESS[props["reuse_permission"]], access_terms=props.get("access_terms_text"))

    def actions(self, context_id: str) -> dto.ActionsResponse:
        pc = self.contexts.get(context_id)
        if pc is None:
            raise NotFound(context_id)
        ctx = pc.record
        targets = {ctx.disease_id, *([ctx.mechanism_id] if ctx.mechanism_id else [])}
        exact = sorted({a.record.subject_id for a in self.assertions.values()
                        if a.record.predicate == "asset_for_context" and a.record.object_id in targets})
        opportunities = []
        for opp in self.c.opportunities:
            if opp.starting_context_id != context_id:
                continue
            partners = [dto.Partner(id=p, label=self._ref(p).label,
                                    contact_url=self.entities[p].properties.get("contact_url")
                                    if self.entities[p].type == "organization" else None)
                        for p in opp.partner_entity_ids]
            opportunities.append(dto.Opportunity(
                id=opp.id, kind=opp.kind, readiness=opp.readiness, asset=self._asset(opp.asset_id), partners=partners,
                basis_assertion_ids=opp.basis_assertion_ids, comparison_ids=opp.comparison_ids,
                known_differences=opp.known_differences, unknowns=opp.unknowns, expert_checks=opp.expert_checks,
                action=dto.Sentence(text=opp.action_text, assertion_ids=opp.basis_assertion_ids, calculation_ids=[],
                                    opportunity_ids=[opp.id]),
                outreach_draft=opp.outreach_draft))
        coverage = {c.id: c for c in self.c.coverage}
        gaps = [dto.Gap(id=g.id, kind=g.kind, description=g.description,
                        coverage=[dto.CoverageRecord(id=c.id, source=c.source, query_or_urls=c.query_or_urls,
                                                     searched_at=c.searched_at, returned_count=c.returned_count,
                                                     inspected_count=c.inspected_count, completion=c.completion,
                                                     failure_reason=c.failure_reason)
                                  for c in (coverage[i] for i in g.coverage_record_ids)],
                        evidence_needed=g.evidence_needed, next_question=g.next_question)
                for g in self.c.gaps if g.context_id == context_id]
        return self._envelope(dto.ActionsResponse, dto.ActionsData(
            context_id=context_id, exact_disease_assets=[self._asset(a) for a in exact],
            opportunities=opportunities, gaps=gaps))


class SnapshotValidationError(RuntimeError):
    pass


def validate_store(store: RealStore, contract_path: Path) -> int:
    """Project every route payload and validate it against the committed contract."""
    from jsonschema import Draft202012Validator

    contract = json.loads(contract_path.read_text(encoding="utf-8"))

    def check(schema_name: str, payload: dto.Dto, what: str) -> None:
        validator = Draft202012Validator({"$ref": f"#/components/schemas/{schema_name}",
                                          "components": contract["components"]})
        errors = list(validator.iter_errors(payload.model_dump(mode="json")))
        if errors:
            raise SnapshotValidationError(f"{what}: {errors[0].message}")

    count = 0
    check("MetaResponse", store.meta(), "meta"); count += 1
    for cid in store.contexts:
        check("ContextResponse", store.context(cid), cid)
        check("ConnectionsResponse", store.connections(cid), cid)
        check("ActionsResponse", store.actions(cid), cid)
        count += 3
    for aid in store.assertions:
        check("AssertionResponse", store.assertion(aid), aid); count += 1
    for cid in store.calculations:
        check("CalculationResponse", store.calculation(cid), cid); count += 1
    for e in list(store.entities.values())[:50]:
        check("SearchResponse", store.search(e.label[:200]), f"search {e.id}"); count += 1
    # Every sentence dependency must resolve inside the snapshot.
    for cid in store.contexts:
        for s in store.context(cid).data.summary:
            if not (s.assertion_ids or s.calculation_ids or s.opportunity_ids):
                raise SnapshotValidationError(f"{cid}: summary sentence without dependencies")
    return count


def load_real_store(settings: Settings) -> RealStore:
    from atlas.api.fixture_store import CONTRACTS_DIR

    if settings.snapshot_backend == "file":
        package = json.loads(Path(settings.snapshot_file).read_text(encoding="utf-8"))
    else:
        from atlas.storage.postgres import load_snapshot_readonly

        package = load_snapshot_readonly(settings.database_url, settings.snapshot_id)
    content = load_package(package, expected_snapshot_id=settings.snapshot_id)
    store = RealStore(settings.snapshot_id, content)
    validate_store(store, CONTRACTS_DIR / "openapi.json")
    return store
