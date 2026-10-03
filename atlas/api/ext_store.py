"""Projection of the proposed 1.1.0 routes from a validated, published package.

Only published records appear. Graph links are either a published assertion
(sourced) or a comparison calculation (computed); context-membership links
come from reviewed context definitions. Clusters are descriptive facets with
their basis stated, not validated disease classes.
"""

from __future__ import annotations

from collections import defaultdict, deque

from atlas.api import dto, dto_ext
from atlas.api.store import MAX_GRAPH_LINKS, MAX_GRAPH_NODES, NotFound

MAX_ENTITY_ASSERTIONS = 200
MAX_OVERLAPS = 10
MAX_SHARED = 20
COMMUNITY_ROLES = {"serves": "community organization", "owns_or_runs": "runs a resource or project",
                   "investigator_on": "investigator on a related project", "funds": "funder of a related project",
                   "asset_for_context": "resource for this context"}


class ExtensionProjection:
    def __init__(self, store) -> None:
        self.s = store
        self.by_entity: dict[str, list] = defaultdict(list)
        for pa in store.assertions.values():
            a = pa.record
            self.by_entity[a.subject_id].append(a)
            self.by_entity[a.object_id].append(a)

    def _env(self, model, data, warnings=None):
        return model(contract_version=dto.CONTRACT_VERSION, snapshot_id=self.s.snapshot_id, data_mode="real",
                     data=data, warnings=warnings or [])

    # ------------------------------------------------------------ entity page

    def entity(self, entity_id: str) -> dto_ext.EntityResponse:
        entity = self.s.entities.get(entity_id)
        if entity is None:
            raise NotFound(entity_id)
        refs = []
        for a in sorted(self.by_entity.get(entity_id, []), key=lambda x: (x.predicate, x.id)):
            outgoing = a.subject_id == entity_id
            other = a.object_id if outgoing else a.subject_id
            refs.append(dto_ext.EntityAssertionRef(
                assertion_id=a.id, predicate=a.predicate, direction="outgoing" if outgoing else "incoming",
                other=self.s._ref(other), other_type=self.s.entities[other].type, statement_status=a.statement_status,
                context_id=a.context_id))
        warnings = []
        if len(refs) > MAX_ENTITY_ASSERTIONS:
            warnings.append(dto.Warning(code="ASSERTIONS_TRUNCATED",
                                        message=f"Showing {MAX_ENTITY_ASSERTIONS} of {len(refs)} published assertions."))
        props = []
        for key, value in sorted(entity.properties.items()):
            if key == "definition_evidence_ids" or value in (None, "", []):
                continue
            props.append(dto_ext.FieldView(name=key, value=", ".join(map(str, value)) if isinstance(value, list) else str(value)))
        return self._env(dto_ext.EntityResponse, dto_ext.EntityData(
            id=entity.id, entity_type=entity.type, label=entity.label,
            aliases=[dto_ext.AliasView(text=a.text, alias_type=a.alias_type) for a in entity.aliases][:50],
            external_ids=entity.external_ids, properties=props,
            contexts=[self.s._option(c) for c in self.s._contexts_for(entity_id)][:20],
            assertions=refs[:MAX_ENTITY_ASSERTIONS], assertion_count=len(refs)), warnings)

    # ------------------------------------------------------------ graph neighborhood

    def _neighbors(self, node: str) -> list[tuple[str, dto_ext.GraphLinkExt]]:
        out = []
        if node in self.s.contexts:
            ctx = self.s.contexts[node].record
            for target in [ctx.disease_id, *ctx.gene_ids, *([ctx.mechanism_id] if ctx.mechanism_id else [])]:
                out.append((target, dto_ext.GraphLinkExt(
                    id=f"scope:{node}:{target}", source=node, target=target, relation="context_scope",
                    assertion_id=None, calculation_id=None, computed=False, statement_status=None)))
            for card in self.s.c.comparisons:
                if card.context_a == node:
                    calc = (card.subgroup_comparison if card.ranking_basis == "subgroup" else card.disease_baseline).calculation_id
                    out.append((card.context_b, dto_ext.GraphLinkExt(
                        id=f"computed:{card.id}", source=node, target=card.context_b,
                        relation=f"phenotype_similarity:{card.ranking_basis}", assertion_id=None, calculation_id=calc,
                        computed=True, statement_status=None)))
            return out
        for a in sorted(self.by_entity.get(node, []), key=lambda x: (x.predicate, x.id)):
            other = a.object_id if a.subject_id == node else a.subject_id
            out.append((other, dto_ext.GraphLinkExt(
                id=f"assertion:{a.id}", source=a.subject_id, target=a.object_id, relation=a.predicate,
                assertion_id=a.id, calculation_id=None, computed=False, statement_status=a.statement_status)))
        for cid, pc in self.s.contexts.items():
            ctx = pc.record
            if node in (ctx.disease_id, ctx.mechanism_id, *ctx.gene_ids):
                out.append((cid, dto_ext.GraphLinkExt(
                    id=f"scope:{cid}:{node}", source=cid, target=node, relation="context_scope", assertion_id=None,
                    calculation_id=None, computed=False, statement_status=None)))
        return out

    def _node(self, node_id: str, focus: str) -> dto_ext.GraphNodeExt:
        if node_id in self.s.contexts:
            return dto_ext.GraphNodeExt(id=node_id, label=self.s.contexts[node_id].record.label, type="context",
                                        is_focus=node_id == focus)
        entity = self.s.entities[node_id]
        return dto_ext.GraphNodeExt(id=node_id, label=entity.label, type=entity.type, is_focus=node_id == focus)

    def graph(self, focus: str, depth: int) -> dto_ext.GraphResponse:
        if focus not in self.s.entities and focus not in self.s.contexts:
            raise NotFound(focus)
        seen = {focus}
        nodes = [focus]
        links: dict[str, dto_ext.GraphLinkExt] = {}
        truncated = False
        queue = deque([(focus, 0)])
        while queue:
            node, d = queue.popleft()
            if d >= depth:
                continue
            for other, link in self._neighbors(node):
                if other not in self.s.entities and other not in self.s.contexts:
                    continue
                if other not in seen:
                    if len(nodes) >= MAX_GRAPH_NODES:
                        truncated = True
                        continue
                    seen.add(other)
                    nodes.append(other)
                    queue.append((other, d + 1))
                if link.id not in links:
                    if len(links) >= MAX_GRAPH_LINKS:
                        truncated = True
                        continue
                    links[link.id] = link
        kept = [l for l in links.values() if l.source in seen and l.target in seen]
        warnings = [dto.Warning(code="GRAPH_TRUNCATED",
                                message=f"Bounded to {MAX_GRAPH_NODES} nodes and {MAX_GRAPH_LINKS} links; open a node to explore further.")] if truncated else []
        legend = [dto_ext.FieldView(name="sourced link", value="a published, reviewed assertion; open assertion_id for evidence"),
                  dto_ext.FieldView(name="computed link", value="a phenotype-similarity calculation; open calculation_id"),
                  dto_ext.FieldView(name="context_scope", value="membership in a reviewed research-context definition"),
                  dto_ext.FieldView(name="mentions", value="the source text names the entity; no relationship asserted")]
        return self._env(dto_ext.GraphResponse, dto_ext.GraphData(
            focus=focus, depth=depth, nodes=[self._node(n, focus) for n in nodes], links=kept, truncated=truncated,
            legend=legend), warnings)

    # ------------------------------------------------------------ clusters

    def clusters(self) -> dto_ext.ClustersResponse:
        clusters: list[dto_ext.Cluster] = []
        clustered: set[str] = set()
        by_effect: dict[str, list[str]] = defaultdict(list)
        for cid, pc in sorted(self.s.contexts.items()):
            ctx = pc.record
            if ctx.mechanism_id:
                effect = self.s.entities[ctx.mechanism_id].properties.get("functional_effect", "unknown")
                if effect != "unknown":
                    by_effect[effect].append(cid)
        for effect, members in sorted(by_effect.items()):
            ids = sorted({a for m in members for a in self.s.contexts[m].record.definition_assertion_ids})
            clusters.append(dto_ext.Cluster(
                id=f"cluster:effect:{effect}", label=f"Recorded {effect.replace('_', '-')}-of-function contexts",
                basis="functional_effect", members=[self.s._option(m) for m in members], assertion_ids=ids,
                calculation_ids=[],
                explanation="Grouped by the functional-effect label in each reviewed mechanism definition. A shared "
                            "label is descriptive; it does not establish equivalent biology across genes."))
            clustered |= set(members)
        by_gene: dict[str, list[str]] = defaultdict(list)
        for cid, pc in sorted(self.s.contexts.items()):
            for gene in pc.record.gene_ids:
                by_gene[gene].append(cid)
        for gene, members in sorted(by_gene.items()):
            if len(members) < 2 or gene not in self.s.entities:
                continue
            diseases = {self.s.contexts[m].record.disease_id for m in members}
            ids = sorted(a.record.id for a in self.s.assertions.values()
                         if a.record.predicate == "gene_associated_with_disease" and a.record.subject_id == gene
                         and a.record.object_id in diseases)
            clusters.append(dto_ext.Cluster(
                id=f"cluster:gene:{gene}", label=f"Contexts involving {self.s.entities[gene].label}",
                basis="shared_gene", members=[self.s._option(m) for m in members], assertion_ids=ids, calculation_ids=[],
                explanation="Research contexts whose reviewed definitions include this gene. One gene can act through "
                            "different mechanisms; check each context's mechanism before inferring shared biology."))
            clustered |= set(members)
        adjacency: dict[str, set[str]] = defaultdict(set)
        calcs: dict[frozenset, str] = {}
        for card in self.s.c.comparisons:
            if card.ranking_basis == "unranked":
                continue
            adjacency[card.context_a].add(card.context_b)
            adjacency[card.context_b].add(card.context_a)
            result = card.subgroup_comparison if card.ranking_basis == "subgroup" else card.disease_baseline
            if result.calculation_id:
                calcs[frozenset((card.context_a, card.context_b))] = result.calculation_id
        seen: set[str] = set()
        for start in sorted(adjacency):
            if start in seen:
                continue
            component, queue = [], deque([start])
            seen.add(start)
            while queue:
                node = queue.popleft()
                component.append(node)
                for nxt in sorted(adjacency[node]):
                    if nxt not in seen:
                        seen.add(nxt)
                        queue.append(nxt)
            if len(component) < 2:
                continue
            members = sorted(component)
            clusters.append(dto_ext.Cluster(
                id=f"cluster:phenotype:{members[0]}", label=f"Phenotype neighborhood around {self.s.contexts[members[0]].record.label}",
                basis="phenotype_neighborhood", members=[self.s._option(m) for m in members], assertion_ids=[],
                calculation_ids=sorted({c for pair, c in calcs.items() if pair <= set(members)}),
                explanation="Connected through each context's top ranked phenotype-similarity neighbors (weighted "
                            "Jaccard over HPO terms with OMIM information content, at one profile level). Exploratory "
                            "clustering through explainable similarity neighborhoods; not a validated disease class."))
            clustered |= set(members)
        unclustered = [self.s._option(c) for c in sorted(self.s.contexts) if c not in clustered]
        return self._env(dto_ext.ClustersResponse, dto_ext.ClustersData(
            method="Three descriptive facets: recorded functional effect, shared gene, and phenotype neighborhoods "
                   "(connected top-ranked similarity links). Contexts can belong to several clusters.",
            clusters=clusters, unclustered=unclustered,
            limitations=["Clusters are browsing aids, not validated mechanistic or clinical groupings.",
                         "Missing mechanism or phenotype evidence leaves a context unclustered rather than guessed."]))

    # ------------------------------------------------------------ network overlap

    def _community(self, context_id: str) -> dict[str, tuple[str, list[str]]]:
        ctx = self.s.contexts[context_id].record
        anchors = {ctx.disease_id, *ctx.gene_ids, *([ctx.mechanism_id] if ctx.mechanism_id else [])}
        found: dict[str, tuple[str, set[str]]] = {}

        def add(entity_id: str, role: str, assertion_id: str) -> None:
            if entity_id in self.s.entities and self.s.entities[entity_id].type in ("person", "organization"):
                current = found.setdefault(entity_id, (role, set()))
                current[1].add(assertion_id)

        studies: set[str] = set()
        for pa in self.s.assertions.values():
            a = pa.record
            if a.predicate == "serves" and a.object_id in anchors:
                add(a.subject_id, COMMUNITY_ROLES["serves"], a.id)
            if a.predicate in ("studies", "mentions") and a.object_id in anchors:
                studies.add(a.subject_id)
            if a.predicate == "asset_for_context" and a.object_id in anchors:
                studies.add(a.subject_id)
        for pa in self.s.assertions.values():
            a = pa.record
            if a.object_id in studies and a.predicate in ("investigator_on", "owns_or_runs", "funds"):
                add(a.subject_id, COMMUNITY_ROLES[a.predicate], a.id)
        return {k: (role, sorted(ids)) for k, (role, ids) in found.items()}

    def network(self, context_id: str) -> dto_ext.NetworkResponse:
        if context_id not in self.s.contexts:
            raise NotFound(context_id)
        mine = self._community(context_id)

        def conn(entity_id, role, ids_a, ids_b):
            entity = self.s.entities[entity_id]
            return dto_ext.SharedConnection(entity=self.s._ref(entity_id), entity_type=entity.type, role=role,
                                            assertion_ids_a=ids_a, assertion_ids_b=ids_b)

        community = [conn(e, role, ids, []) for e, (role, ids) in
                     sorted(mine.items(), key=lambda kv: (self.s.entities[kv[0]].type != "person", kv[0]))][:MAX_SHARED]
        mine_ctx = self.s.contexts[context_id].record
        overlaps = []
        for other in sorted(self.s.contexts):
            other_ctx = self.s.contexts[other].record
            # Only communities that are otherwise unconnected: different disease and no shared gene.
            if other == context_id or other_ctx.disease_id == mine_ctx.disease_id or set(other_ctx.gene_ids) & set(mine_ctx.gene_ids):
                continue
            theirs = self._community(other)
            shared = sorted(set(mine) & set(theirs), key=lambda e: (self.s.entities[e].type != "person", e))
            shared = [e for e in shared if not e.startswith("org:nih-")]  # a common federal funder is not a meaningful bridge
            if shared:
                overlaps.append(dto_ext.OverlapItem(
                    other_context=self.s._option(other),
                    shared=[conn(e, mine[e][0], mine[e][1], theirs[e][1]) for e in shared][:MAX_SHARED]))
        overlaps.sort(key=lambda o: (-sum(1 for s in o.shared if s.entity_type == "person"), o.other_context.id))
        return self._env(dto_ext.NetworkResponse, dto_ext.NetworkData(
            context_id=context_id, community=community, overlaps=overlaps[:MAX_OVERLAPS],
            limitations=["Overlaps list only contexts with a different disease and no shared gene, i.e. otherwise "
                         "unconnected communities.",
                         "People and organizations come from published registry, funding and community records; "
                         "a shared investigator or organization is a lead to ask about, not an endorsement or "
                         "confirmed collaboration.",
                         "Project links via 'mentions' only mean the project text names the gene."]))
