"""One read interface shared by the fixture loader and the real snapshot reader.

Route handlers depend only on `SnapshotStore`. Neither implementation performs
network calls, model calls, downloads or writes while serving a request.
"""

from __future__ import annotations

from typing import Protocol

from atlas.api import dto


class NotFound(LookupError):
    """The requested ID is not part of the pinned snapshot."""


MAX_SEARCH_MATCHES = 20
MAX_CONTEXTS_PER_MATCH = 20
MAX_COMPARISONS = 3
MAX_GRAPH_NODES = 60
MAX_GRAPH_LINKS = 100


class SnapshotStore(Protocol):
    snapshot_id: str
    data_mode: str

    def meta(self) -> dto.MetaResponse: ...
    def search(self, query: str) -> dto.SearchResponse: ...
    def context(self, context_id: str) -> dto.ContextResponse: ...
    def connections(self, context_id: str) -> dto.ConnectionsResponse: ...
    def actions(self, context_id: str) -> dto.ActionsResponse: ...
    def assertion(self, assertion_id: str) -> dto.AssertionResponse: ...
    def calculation(self, calculation_id: str) -> dto.CalculationResponse: ...


def bound_search(matches: list[dto.SearchMatch]) -> tuple[list[dto.SearchMatch], list[dto.Warning]]:
    """Apply the contract caps and record truncation explicitly."""
    warnings: list[dto.Warning] = []
    bounded: list[dto.SearchMatch] = []
    for match in matches[:MAX_SEARCH_MATCHES]:
        if len(match.contexts) > MAX_CONTEXTS_PER_MATCH:
            warnings.append(dto.Warning(
                code="CONTEXTS_TRUNCATED",
                message=f"{match.id}: showing {MAX_CONTEXTS_PER_MATCH} of {len(match.contexts)} research contexts.",
            ))
            match = match.model_copy(update={"contexts": match.contexts[:MAX_CONTEXTS_PER_MATCH]})
        bounded.append(match)
    if len(matches) > MAX_SEARCH_MATCHES:
        warnings.append(dto.Warning(
            code="SEARCH_TRUNCATED",
            message=f"Showing {MAX_SEARCH_MATCHES} of {len(matches)} matches; refine the query.",
        ))
    return bounded, warnings
