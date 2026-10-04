"""Read-only tools the research assistant can call, backed by the published snapshot.

Each tool returns the same projection the public read API serves (validated
DTOs), trimmed so a single result stays small. Tool outputs are the only
material the assistant may cite; every ID they contain is recorded so the
answer's citations can be checked afterwards.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

from atlas.api.store import NotFound

MAX_LIST = 25
MAX_TOOL_CHARS = 14_000

_STRING_RE = re.compile(r'"((?:[^"\\]|\\.){1,300})"')
_ID = {"type": "string", "minLength": 1, "maxLength": 180}


def _fn(name: str, description: str, properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "function", "name": name, "description": description, "strict": True,
            "parameters": {"type": "object", "properties": properties, "required": list(properties),
                           "additionalProperties": False}}


TOOL_SPECS: list[dict[str, Any]] = [
    _fn("search", "Global search over diseases, genes, symptoms (HPO), mechanisms, organizations, studies and assets, "
        "with synonym resolution. Returns matches and the research contexts each opens.", {"query": _ID}),
    _fn("get_context", "Summary of one research context: disease, genes, recorded mechanism (null = unknown), "
        "disease-level and subgroup-level symptom profiles, limitations.", {"context_id": _ID}),
    _fn("get_connections", "Up to three comparison cards for a context: phenotype similarity to other contexts at "
        "disease-baseline and subgroup level, shared and differing terms, mechanism features, counterexamples.",
        {"context_id": _ID}),
    _fn("get_actions", "Patient action view for a context: existing resources for the exact disease, possible leads "
        "and evidence gaps.", {"context_id": _ID}),
    _fn("get_network", "People and organizations around a context and overlaps with otherwise unconnected "
        "communities (shared investigators, organizations).", {"context_id": _ID}),
    _fn("get_entity", "Any node (gene, disease, phenotype, mechanism, organization, person, study, asset) with its "
        "published assertions and contexts.", {"entity_id": _ID}),
    _fn("get_assertion", "One published statement with its evidence (quoted text or structured record), source, "
        "review status and conflicts. Use before relying on a statement.", {"assertion_id": _ID}),
    _fn("get_calculation", "A computed similarity: algorithm, parameters, inputs, result and explanation.",
        {"calculation_id": _ID}),
    _fn("find_paths", "Up to three shortest evidence paths between two nodes (entity or context IDs); each step is "
        "a published assertion, computed comparison or context definition.",
        {"from_id": _ID, "to_id": _ID, "max_length": {"type": "integer", "minimum": 1, "maximum": 5}}),
    _fn("get_graph", "Bounded graph neighborhood around a node (depth 1 or 2).",
        {"focus_id": _ID, "depth": {"type": "integer", "enum": [1, 2]}}),
    _fn("get_clusters", "Explainable cluster facets: recorded functional effect, shared gene, phenotype "
        "neighborhoods.", {}),
    _fn("get_meta", "Snapshot metadata: sources and versions, counts, example contexts, release limitations.", {}),
]


def _trim(value: Any, limit: int = MAX_LIST) -> Any:
    if isinstance(value, list):
        items = [_trim(v, limit) for v in value[:limit]]
        if len(value) > limit:
            items.append({"_truncated": f"{len(value) - limit} more items not shown"})
        return items
    if isinstance(value, dict):
        return {k: _trim(v, limit) for k, v in value.items()}
    return value


def _strings(value: Any, out: set[str]) -> None:
    if isinstance(value, str):
        out.add(value)
    elif isinstance(value, list):
        for v in value:
            _strings(v, out)
    elif isinstance(value, dict):
        for v in value.values():
            _strings(v, out)


class Toolbox:
    """Executes tool calls against one store and remembers every ID the assistant has been shown."""

    def __init__(self, store) -> None:
        self.store = store
        self.seen: set[str] = set()
        self.calls: list[dict[str, Any]] = []
        ext = getattr(store, "ext", None)
        self._handlers: dict[str, Callable[..., Any]] = {
            "search": lambda query: store.search(query),
            "get_context": lambda context_id: store.context(context_id),
            "get_connections": lambda context_id: store.connections(context_id),
            "get_actions": lambda context_id: store.actions(context_id),
            "get_assertion": lambda assertion_id: store.assertion(assertion_id),
            "get_calculation": lambda calculation_id: store.calculation(calculation_id),
            "get_meta": lambda: store.meta(),
        }
        if ext is not None:
            self._handlers |= {
                "get_network": lambda context_id: ext.network(context_id),
                "get_entity": lambda entity_id: ext.entity(entity_id),
                "find_paths": lambda from_id, to_id, max_length: ext.paths(from_id, to_id, max_length),
                "get_graph": lambda focus_id, depth: ext.graph(focus_id, depth),
                "get_clusters": lambda: ext.clusters(),
            }

    def run(self, name: str, arguments: str) -> str:
        try:
            args = json.loads(arguments or "{}")
            if not isinstance(args, dict):
                raise ValueError("arguments must be an object")
        except ValueError as exc:
            return json.dumps({"error": f"invalid arguments: {exc}"})
        self.calls.append({"name": name, "arguments": args})
        handler = self._handlers.get(name)
        if handler is None:
            return json.dumps({"error": f"unknown tool {name}"})
        try:
            body = handler(**args).model_dump(mode="json")
        except NotFound:
            return json.dumps({"error": "not found in the published snapshot", "arguments": args})
        except (TypeError, ValueError) as exc:
            return json.dumps({"error": f"invalid request: {exc}"})
        for limit in (MAX_LIST, 12, 6, 3):  # shorten lists until the result fits, keeping valid JSON
            payload = {"data": _trim(body.get("data"), limit), "warnings": body.get("warnings", [])}
            text = json.dumps(payload, ensure_ascii=False)
            if len(text) <= MAX_TOOL_CHARS:
                _strings(payload, self.seen)
                return text
        text = text[:MAX_TOOL_CHARS]
        for match in _STRING_RE.findall(text):  # cite only what was actually shown
            try:
                self.seen.add(json.loads(f'"{match}"'))
            except ValueError:
                continue
        return text + " ...(output truncated; ask a narrower question)"

    # ---- citation checks (an ID counts only if the assistant was shown it and it resolves)

    def valid_assertion(self, assertion_id: str) -> bool:
        return assertion_id in self.seen and assertion_id in getattr(self.store, "assertions", {})

    def valid_calculation(self, calculation_id: str) -> bool:
        return calculation_id in self.seen and calculation_id in getattr(self.store, "calculations", {})

    def valid_node(self, node_id: str) -> bool:
        return node_id in self.seen and (node_id in getattr(self.store, "entities", {})
                                         or node_id in getattr(self.store, "contexts", {}))
