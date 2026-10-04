"""Explicitly synthetic fixture replay (`ATLAS_DATA_MODE=synthetic_fixture`).

Serves the supplied invented transport fixtures verbatim. Their IDs and hashes
are illustrative, never content-addressed real publications, and every
response carries the fixtures' own SYNTHETIC_DATA warning. Optional scenario
overlays (`ATLAS_FIXTURE_SCENARIOS`) swap in the isolated component examples
(contested, partial_reference, preprint, structured_record) for UI testing;
they are not a scientifically consistent merged snapshot.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from atlas.api import dto
from atlas.api.store import NotFound, bound_search
from atlas.hashing import sha256_bytes

CONTRACTS_DIR = Path(__file__).resolve().parents[2] / "contracts"
# Checksums recorded in the handoff VALIDATION_REPORT.md for contract 1.0.0.
EXPECTED_SHA256 = {
    "openapi.json": "3eedc557dc8be2e7d82e3bb00213f6a7fa906141de72b246fe115771b53b845c",
    "fixtures.json": "ca02ddde6de94dcbeac208d154c7e49df4cf1b1e196e2db8489c133455f34355",
}
OVERLAY_SCENARIOS = ("contested", "partial_reference", "preprint", "structured_record")


class FixtureIntegrityError(RuntimeError):
    pass


def verify_contract_files(directory: Path = CONTRACTS_DIR) -> None:
    for name, expected in EXPECTED_SHA256.items():
        actual = sha256_bytes((directory / name).read_bytes())
        if actual != expected:
            raise FixtureIntegrityError(f"{name} checksum {actual} does not match contract 1.0.0 ({expected})")


class FixtureStore:
    data_mode = "synthetic_fixture"

    def __init__(self, scenarios: tuple[str, ...] = (), directory: Path = CONTRACTS_DIR) -> None:
        verify_contract_files(directory)
        unknown = [s for s in scenarios if s not in OVERLAY_SCENARIOS]
        if unknown:
            raise FixtureIntegrityError(f"Unknown fixture scenarios {unknown}; allowed: {OVERLAY_SCENARIOS}")
        bundle = json.loads((directory / "fixtures.json").read_text(encoding="utf-8"))
        self._by_route: dict[str, dict] = {}
        self._search: dict[str, dict] = {}
        self._schemas: dict[str, str] = {}
        enabled = ("default", "no_results", *scenarios)
        for scenario in enabled:
            for item in bundle["manifest"]["fixtures"]:
                if item["scenario"] != scenario or item["status"] != 200 or not item["route"].startswith("/v1/"):
                    continue
                body = bundle["responses"][item["file"]]
                parts = urlsplit(item["route"])
                if parts.path == "/v1/search":
                    self._search[parse_qs(parts.query)["q"][0]] = body
                else:
                    self._by_route[parts.path] = body
                self._schemas[parts.path] = item["schema"]
        meta = self._by_route["/v1/meta"]
        self.snapshot_id: str = meta["snapshot_id"]
        self._warnings = [dto.Warning(**w) for w in meta["warnings"]]

    @property
    def ext(self) -> "FixtureExtension":
        return FixtureExtension(CONTRACTS_DIR / "extension-1.1.0" / "fixtures.json")

    def _get(self, path: str, model: type[dto.Dto]):
        body = self._by_route.get(path)
        if body is None:
            raise NotFound(path)
        return model.model_validate(body)

    def meta(self) -> dto.MetaResponse:
        return self._get("/v1/meta", dto.MetaResponse)

    def search(self, query: str) -> dto.SearchResponse:
        if query in self._search:
            return dto.SearchResponse.model_validate(self._search[query])
        # Filter the invented fixture entities by plain substring retrieval; the
        # result is still synthetic transport data, never a real lookup.
        needle = query.casefold()
        seen: set[str] = set()
        matches: list[dto.SearchMatch] = []
        for body in self._search.values():
            for raw in body["data"]["matches"]:
                match = dto.SearchMatch.model_validate(raw)
                haystack = [match.id, match.label, match.matched_alias or ""]
                if match.id not in seen and any(needle in h.casefold() for h in haystack):
                    seen.add(match.id)
                    matches.append(match)
        bounded, warnings = bound_search(matches)
        return dto.SearchResponse(
            contract_version=dto.CONTRACT_VERSION, snapshot_id=self.snapshot_id, data_mode="synthetic_fixture",
            data=dto.SearchData(query=query, matches=bounded), warnings=[*self._warnings, *warnings],
        )

    def context(self, context_id: str) -> dto.ContextResponse:
        return self._get(f"/v1/contexts/{context_id}", dto.ContextResponse)

    def connections(self, context_id: str) -> dto.ConnectionsResponse:
        return self._get(f"/v1/contexts/{context_id}/connections", dto.ConnectionsResponse)

    def actions(self, context_id: str) -> dto.ActionsResponse:
        return self._get(f"/v1/contexts/{context_id}/actions", dto.ActionsResponse)

    def assertion(self, assertion_id: str) -> dto.AssertionResponse:
        return self._get(f"/v1/assertions/{assertion_id}", dto.AssertionResponse)

    def calculation(self, calculation_id: str) -> dto.CalculationResponse:
        return self._get(f"/v1/calculations/{calculation_id}", dto.CalculationResponse)


class FixtureExtension:
    """Synthetic examples for the proposed 1.1.0 routes; unknown IDs are 404 like any fixture route."""

    def __init__(self, path: Path) -> None:
        self.bodies = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def _get(self, route: str, model_name: str):
        from atlas.api import dto_ext

        body = self.bodies.get(route)
        if body is None:
            raise NotFound(route)
        return dto_ext.EXTENSION_MODELS[model_name].model_validate(body)

    def entity(self, entity_id: str):
        return self._get(f"/v1/entities/{entity_id}", "EntityResponse")

    def graph(self, focus: str, depth: int):
        return self._get(f"/v1/graph?focus={focus}&depth={depth}", "GraphResponse")

    def paths(self, from_id: str, to_id: str, max_length: int):
        return self._get(f"/v1/paths?from={from_id}&to={to_id}&max_length={max_length}", "PathsResponse")

    def atlas_map(self):
        return self._get("/v1/atlas-map", "AtlasMapResponse")

    def clusters(self):
        return self._get("/v1/clusters", "ClustersResponse")

    def network(self, context_id: str):
        return self._get(f"/v1/contexts/{context_id}/network", "NetworkResponse")
