"""Proposed contract 1.1.0 additions (additive; 1.0.0 routes and shapes are unchanged).

Six read-only routes the challenge interface needs beyond the per-context
cards of 1.0.0: an entity page, a bounded knowledge-graph neighborhood, a
whole-atlas map of every context and its connections, shortest evidence
paths between two nodes, explainable cluster
facets and community/researcher network overlap. They use
the same envelope (contract_version stays "1.0.0" for envelope compatibility),
the same error envelope and the same bounds. Every sourced link names its
assertion; every computed link names its calculation.
"""

from __future__ import annotations

from typing import Literal

from atlas.api.dto import ContextOption, ContractVersion, DataMode, Dto, EntityRef, EntityType, Warning

EXTENSION_VERSION = "1.1.0-proposed"


class AliasView(Dto):
    text: str
    alias_type: str


class FieldView(Dto):
    name: str
    value: str


class EntityAssertionRef(Dto):
    assertion_id: str
    predicate: str
    direction: Literal["outgoing", "incoming"]
    other: EntityRef
    other_type: EntityType
    statement_status: str
    context_id: str | None


class EntityData(Dto):
    id: str
    entity_type: EntityType
    label: str
    aliases: list[AliasView]
    external_ids: list[str]
    properties: list[FieldView]
    contexts: list[ContextOption]
    assertions: list[EntityAssertionRef]
    assertion_count: int


class GraphNodeExt(Dto):
    id: str
    label: str
    type: str  # an entity type, or "context"
    is_focus: bool


class GraphLinkExt(Dto):
    id: str
    source: str
    target: str
    relation: str
    assertion_id: str | None
    calculation_id: str | None
    computed: bool
    statement_status: str | None


class GraphData(Dto):
    focus: str
    depth: int
    nodes: list[GraphNodeExt]
    links: list[GraphLinkExt]
    truncated: bool
    legend: list[FieldView]


class AtlasMapData(Dto):
    nodes: list[GraphNodeExt]
    links: list[GraphLinkExt]
    truncated: bool
    contexts_without_similarity: list[ContextOption]
    legend: list[FieldView]
    limitations: list[str]


class EvidencePath(Dto):
    length: int
    nodes: list[GraphNodeExt]
    links: list[GraphLinkExt]


class PathsData(Dto):
    from_id: str
    to_id: str
    max_length: int
    paths: list[EvidencePath]
    limitations: list[str]


class Cluster(Dto):
    id: str
    label: str
    basis: Literal["functional_effect", "shared_gene", "shared_process", "phenotype_neighborhood"]
    members: list[ContextOption]
    assertion_ids: list[str]
    calculation_ids: list[str]
    explanation: str


class ClustersData(Dto):
    method: str
    clusters: list[Cluster]
    unclustered: list[ContextOption]
    limitations: list[str]


class SharedConnection(Dto):
    entity: EntityRef
    entity_type: EntityType
    role: str
    assertion_ids_a: list[str]
    assertion_ids_b: list[str]


class OverlapItem(Dto):
    other_context: ContextOption
    shared: list[SharedConnection]


class NetworkData(Dto):
    context_id: str
    community: list[SharedConnection]
    overlaps: list[OverlapItem]
    limitations: list[str]


class _Envelope(Dto):
    contract_version: ContractVersion
    snapshot_id: str
    data_mode: DataMode
    warnings: list[Warning]


class EntityResponse(_Envelope):
    data: EntityData


class GraphResponse(_Envelope):
    data: GraphData


class AtlasMapResponse(_Envelope):
    data: AtlasMapData


class PathsResponse(_Envelope):
    data: PathsData


class ClustersResponse(_Envelope):
    data: ClustersData


class NetworkResponse(_Envelope):
    data: NetworkData


EXTENSION_MODELS = {"EntityResponse": EntityResponse, "GraphResponse": GraphResponse,
                    "AtlasMapResponse": AtlasMapResponse, "PathsResponse": PathsResponse,
                    "ClustersResponse": ClustersResponse, "NetworkResponse": NetworkResponse}
