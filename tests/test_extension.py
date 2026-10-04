"""Proposed additive 1.1.0 routes: entity, graph, clusters, network (offline-unit, real HTTP)."""

from __future__ import annotations

import json

import httpx
import pytest
from jsonschema import Draft202012Validator

from atlas.api.main import create_app
from atlas.api.settings import load_settings
from atlas.assembly.assemble import write_package

from .conftest import ROOT, LiveServer
from .synthetic_world import build_full_world

EXT = ROOT / "contracts" / "extension-1.1.0"


@pytest.fixture(scope="module")
def ext_contract():
    return json.loads((EXT / "openapi-extension.json").read_text())


def _validate(contract, schema_name, body):
    validator = Draft202012Validator({"$ref": f"#/components/schemas/{schema_name}", "components": contract["components"]})
    errors = list(validator.iter_errors(body))
    assert not errors, errors[:2]


@pytest.fixture
def real(tmp_path):
    world, package, _ = build_full_world(tmp_path / "ws")
    path = write_package(package, world.ws.snapshots)
    env = {"ATLAS_DATA_MODE": "real", "ATLAS_SNAPSHOT_ID": package["snapshot_id"], "ATLAS_SNAPSHOT_BACKEND": "file",
           "ATLAS_SNAPSHOT_FILE": path}
    with LiveServer(create_app(load_settings(env))) as live:
        yield live, package


def test_extension_routes_validate_and_every_link_has_provenance(real, ext_contract):
    live, package = real
    calcs = {c["id"] for c in package["content"]["calculations"]}
    assertions = {a["record"]["id"] for a in package["content"]["assertions"]}
    for focus in ("ctx:syn-a-loss", "HGNC:900001", "org:syn-beta"):
        for depth in (1, 2):
            r = httpx.get(live.url + "/v1/graph", params={"focus": focus, "depth": depth})
            assert r.status_code == 200
            body = r.json()
            _validate(ext_contract, "GraphResponse", body)
            data = body["data"]
            assert len(data["nodes"]) <= 60 and len(data["links"]) <= 100
            node_ids = {n["id"] for n in data["nodes"]}
            assert [n["id"] for n in data["nodes"] if n["is_focus"]] == [focus]
            for link in data["links"]:
                assert link["source"] in node_ids and link["target"] in node_ids
                if link["computed"]:
                    assert link["calculation_id"] in calcs and link["assertion_id"] is None
                elif link["relation"] == "context_scope":
                    assert link["assertion_id"] is None and link["calculation_id"] is None
                else:
                    assert link["assertion_id"] in assertions
    atlas_map = httpx.get(live.url + "/v1/atlas-map").json()
    _validate(ext_contract, "AtlasMapResponse", atlas_map)
    data = atlas_map["data"]
    node_ids = {n["id"] for n in data["nodes"]}
    contexts = {c["record"]["id"] for c in package["content"]["contexts"]}
    assert contexts <= node_ids  # every published context lands on the map
    assert any(link["computed"] for link in data["links"])
    for link in data["links"]:
        assert link["source"] in node_ids and link["target"] in node_ids
        if link["computed"]:
            assert link["calculation_id"] in calcs and link["assertion_id"] is None
        elif link["relation"] != "context_scope":
            assert link["assertion_id"] in assertions
    unlinked = {c["id"] for c in data["contexts_without_similarity"]}
    assert all(link["source"] not in unlinked and link["target"] not in unlinked
               for link in data["links"] if link["computed"])
    paths = httpx.get(live.url + "/v1/paths", params={"from": "ctx:syn-a-loss", "to": "org:syn-beta"}).json()
    _validate(ext_contract, "PathsResponse", paths)
    assert paths["data"]["paths"], "a path exists in the synthetic world"
    for path in paths["data"]["paths"]:
        ids = [n["id"] for n in path["nodes"]]
        assert ids[0] == "ctx:syn-a-loss" and ids[-1] == "org:syn-beta" and len(path["links"]) == path["length"]
        for link, (a, b) in zip(path["links"], zip(ids, ids[1:])):
            assert {link["source"], link["target"]} == {a, b} and link["relation"] != "mentions"
            assert link["calculation_id"] in calcs if link["computed"] else (
                link["relation"] == "context_scope" or link["assertion_id"] in assertions)
    none = httpx.get(live.url + "/v1/paths", params={"from": "ctx:syn-a-loss", "to": "org:syn-beta", "max_length": 1}).json()
    assert none["data"]["paths"] == [] and none["warnings"][0]["code"] == "NO_PATH"
    entity = httpx.get(live.url + "/v1/entities/HGNC:900001").json()
    _validate(ext_contract, "EntityResponse", entity)
    assert {c["id"] for c in entity["data"]["contexts"]} >= {"ctx:syn-a-unknown", "ctx:syn-a-loss"}
    clusters = httpx.get(live.url + "/v1/clusters").json()
    _validate(ext_contract, "ClustersResponse", clusters)
    bases = {c["basis"] for c in clusters["data"]["clusters"]}
    assert bases == {"functional_effect", "shared_gene", "phenotype_neighborhood"}
    for c in clusters["data"]["clusters"]:
        assert c["explanation"] and set(c["calculation_ids"]) <= calcs and set(c["assertion_ids"]) <= assertions
    network = httpx.get(live.url + "/v1/contexts/ctx:syn-a-unknown/network").json()
    _validate(ext_contract, "NetworkResponse", network)
    assert network["data"]["community"][0]["entity"]["id"] == "org:syn-alpha"
    for route, status in (("/v1/graph", 422), ("/v1/graph?focus=x&depth=3", 422), ("/v1/graph?focus=nope", 404),
                          ("/v1/entities/nope", 404), ("/v1/paths?from=x", 422),
                          ("/v1/paths?from=nope&to=ctx:syn-a-loss", 404), ("/v1/paths?from=a&to=b&max_length=9", 422), ("/v1/contexts/nope/network", 404)):
        response = httpx.get(live.url + route)
        assert response.status_code == status, route
        assert response.json()["error"]["code"] in ("INVALID_REQUEST", "NOT_FOUND")
    assert httpx.get(live.url + "/openapi-extension.json").json()["info"]["version"] == "1.1.0-proposed"


def test_fixture_mode_serves_labelled_synthetic_extension_examples(ext_contract):
    app = create_app(load_settings({"ATLAS_DATA_MODE": "synthetic_fixture"}))
    examples = json.loads((EXT / "fixtures.json").read_text())
    with LiveServer(app) as live:
        for route, body in examples.items():
            response = httpx.get(live.url + route)
            assert response.status_code == 200 and response.json() == body
            assert body["data_mode"] == "synthetic_fixture" and body["warnings"][0]["code"] == "SYNTHETIC_DATA"
        assert httpx.get(live.url + "/v1/entities/HGNC:1").status_code == 404


def test_committed_extension_contract_and_examples_match_the_code():
    import importlib.util

    spec = importlib.util.spec_from_file_location("gen", ROOT / "scripts" / "generate_extension_contract.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    assert json.loads((EXT / "openapi-extension.json").read_text()) == json.loads(json.dumps(gen.openapi()))
    assert json.loads((EXT / "fixtures.json").read_text()) == json.loads(json.dumps(gen.fixtures()))
    # The shared 1.0.0 contract is untouched.
    assert (ROOT / "contracts" / "openapi.json").read_bytes() == (
        ROOT / "Rare_Disease_Atlas_Backend_V2.2" / "contracts" / "openapi.json").read_bytes()
