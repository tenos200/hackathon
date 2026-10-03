"""Generate contracts/extension-1.1.0/{openapi-extension.json,fixtures.json} (proposed, additive).

The OpenAPI document is generated from atlas/api/dto_ext.py. Example responses
are projected from the SYNTHETIC test world and relabelled synthetic_fixture
with the SYNTHETIC_DATA warning; they are interface examples, not research.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from atlas.api import dto_ext  # noqa: E402
from atlas.hashing import canonical_json  # noqa: E402

OUT = ROOT / "contracts" / "extension-1.1.0"
SYNTHETIC = {"code": "SYNTHETIC_DATA", "message": "Invented interface fixtures. Not scientific evidence."}


def openapi() -> dict:
    components: dict = {}
    for name, model in dto_ext.EXTENSION_MODELS.items():
        schema = model.model_json_schema(ref_template="#/components/schemas/{model}", mode="serialization")
        components.update(schema.pop("$defs", {}))
        components[name] = schema
    error = {"$ref": "https://atlas.invalid/contracts/openapi.json#/components/schemas/ErrorEnvelope"}
    errors = {str(code): {"description": "Shared 1.0.0 error envelope", "content": {"application/json": {"schema": error}}}
              for code in (404, 422, 429, 500, 503)}

    def op(op_id, schema, params, summary):
        return {"get": {"operationId": op_id, "summary": summary, "parameters": params, "responses": {
            "200": {"description": "OK", "content": {"application/json": {"schema": {"$ref": f"#/components/schemas/{schema}"}}}},
            **errors}}}

    id_param = {"name": "id", "in": "path", "required": True, "schema": {"type": "string", "minLength": 1, "maxLength": 180}}
    return {
        "openapi": "3.1.0",
        "info": {"title": "Rare Disease Atlas public read API: proposed additive extension", "version": dto_ext.EXTENSION_VERSION,
                 "description": "Additive to contract 1.0.0 (unchanged). Same envelope, errors, bounds and CORS. "
                                "Proposal: contracts/extension-1.1.0/PROPOSAL.md."},
        "paths": {
            "/v1/entities/{id}": op("getEntity", "EntityResponse", [id_param], "Any published entity with its assertions"),
            "/v1/graph": op("getGraph", "GraphResponse", [
                {"name": "focus", "in": "query", "required": True, "schema": {"type": "string", "minLength": 1, "maxLength": 180}},
                {"name": "depth", "in": "query", "required": False, "schema": {"type": "integer", "enum": [1, 2], "default": 1}}],
                "Bounded knowledge-graph neighborhood (60 nodes / 100 links)"),
            "/v1/clusters": op("getClusters", "ClustersResponse", [], "Explainable cluster facets"),
            "/v1/contexts/{id}/network": op("getNetwork", "NetworkResponse", [id_param], "Community and researcher overlap"),
        },
        "components": {"schemas": components},
    }


def fixtures() -> dict:
    from tests.conftest import real_store_for
    from tests.synthetic_world import build_full_world

    with tempfile.TemporaryDirectory() as tmp:
        _, package, _ = build_full_world(Path(tmp) / "ws")
        ext = real_store_for(package).ext
        bodies = {
            "/v1/entities/HGNC:900001": ext.entity("HGNC:900001"),
            "/v1/graph?focus=ctx:syn-a-loss&depth=1": ext.graph("ctx:syn-a-loss", 1),
            "/v1/graph?focus=ctx:syn-a-loss&depth=2": ext.graph("ctx:syn-a-loss", 2),
            "/v1/clusters": ext.clusters(),
            "/v1/contexts/ctx:syn-a-unknown/network": ext.network("ctx:syn-a-unknown"),
        }
    out = {}
    for route, response in bodies.items():
        body = response.model_dump(mode="json")
        body["snapshot_id"] = "snap_fixture_extension_20261003"
        body["data_mode"] = "synthetic_fixture"
        body["warnings"] = [SYNTHETIC, *body["warnings"]]
        out[route] = body
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "openapi-extension.json").write_text(json.dumps(openapi(), indent=2, sort_keys=True) + "\n")
    (OUT / "fixtures.json").write_text(json.dumps(fixtures(), indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
