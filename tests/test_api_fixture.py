"""Fixture-mode API over real HTTP, validated against the committed contract.

These are interface checks with invented fixtures (boundary: offline-fixture).
They do not evaluate scientific content.
"""

from __future__ import annotations

import json
from urllib.parse import quote, urlsplit

import httpx
import pytest

from atlas.api import dto
from atlas.api.main import create_app
from atlas.api.settings import load_settings
from atlas.api.store import NotFound

from .conftest import ROOT, LiveServer, endpoint_schema, schema_validator

FIXTURE_ENV = {"ATLAS_DATA_MODE": "synthetic_fixture", "ATLAS_ALLOWED_ORIGINS": '["http://localhost:5173"]'}


@pytest.fixture(scope="module")
def server():
    with LiveServer(create_app(load_settings(FIXTURE_ENV))) as live:
        yield live


def _encoded(route: str) -> str:
    parts = urlsplit(route)
    segments = parts.path.split("/")
    path = "/".join(quote(s, safe=":") for s in segments)
    return path + (f"?{parts.query}" if parts.query else "")


def _validate(contract, path, status, body):
    errors = list(schema_validator(contract, endpoint_schema(contract, path, status)).iter_errors(body))
    assert not errors, errors[:3]


@pytest.mark.master("T22", boundary="offline-fixture")
@pytest.mark.master("T37", boundary="offline-fixture")
def test_every_default_fixture_route_served_verbatim_and_valid(server, contract, fixture_bundle):
    checked = 0
    for item in fixture_bundle["manifest"]["fixtures"]:
        if item["scenario"] not in ("default", "no_results") or item["status"] != 200:
            continue
        response = httpx.get(server.url + _encoded(item["route"]), headers={"Accept": "application/json"})
        assert response.status_code == 200, item
        body = response.json()
        path = urlsplit(item["route"]).path
        if path.startswith("/v1/"):
            _validate(contract, path, 200, body)
        assert body == fixture_bundle["responses"][item["file"]], item["file"]
        checked += 1
    assert checked == 34  # 32 product + healthz + readyz


@pytest.mark.master("T22", boundary="offline-fixture")
def test_scenario_overlays_serve_isolated_examples(contract, fixture_bundle):
    scenarios = {"contested": "assertion_contested.json", "partial_reference": "connections_partial_reference.json",
                 "preprint": "assertion_preprint.json", "structured_record": "assertion_structured.json"}
    for scenario, file in scenarios.items():
        item = next(i for i in fixture_bundle["manifest"]["fixtures"] if i["file"] == file)
        app = create_app(load_settings({**FIXTURE_ENV, "ATLAS_FIXTURE_SCENARIOS": scenario}))
        with LiveServer(app) as live:
            body = httpx.get(live.url + _encoded(item["route"])).json()
        _validate(contract, urlsplit(item["route"]).path, 200, body)
        assert body == fixture_bundle["responses"][file]


@pytest.mark.master("T22", boundary="offline-fixture")
@pytest.mark.parametrize("route", ["/v1/search", "/v1/search?q=", "/v1/search?q=%20%20%20",
                                   "/v1/search?q=" + "x" * 201, "/v1/contexts/" + "c" * 181])
def test_invalid_input_uses_shared_422_envelope(server, contract, route):
    response = httpx.get(server.url + route)
    assert response.status_code == 422
    body = response.json()
    _validate(contract, "/v1/search", 422, body)
    assert body["error"]["code"] == "INVALID_REQUEST" and body["error"]["retryable"] is False
    assert "detail" not in body
    assert body["error"]["request_id"] == response.headers["x-request-id"]


@pytest.mark.master("T22", boundary="offline-fixture")
def test_unknown_ids_are_404_envelopes(server, contract):
    for route in ["/v1/contexts/missing", "/v1/contexts/missing/connections", "/v1/contexts/missing/actions",
                  "/v1/assertions/assert:nope", "/v1/calculations/calc:nope", "/v1/not-a-route"]:
        response = httpx.get(server.url + route)
        assert response.status_code == 404, route
        body = response.json()
        _validate(contract, "/v1/meta", 404, body)
        assert body["error"]["code"] == "NOT_FOUND" and body["snapshot_id"] == "snap_fixture_20261003"


@pytest.mark.master("T22", boundary="offline-fixture")
def test_successful_empty_search_is_200(server):
    body = httpx.get(server.url + "/v1/search", params={"q": "  unknown-fixture  "}).json()
    assert body["data"] == {"query": "unknown-fixture", "matches": []}
    other = httpx.get(server.url + "/v1/search", params={"q": "zzz-nothing"}).json()
    assert other["data"]["matches"] == [] and other["data_mode"] == "synthetic_fixture"


@pytest.mark.master("T22", boundary="offline-fixture")
def test_rate_limit_returns_429_envelope_with_cors(contract):
    app = create_app(load_settings({**FIXTURE_ENV, "ATLAS_RATE_LIMIT_PER_MINUTE": "2"}))
    with LiveServer(app) as live:
        headers = {"Origin": "http://localhost:5173"}
        statuses = [httpx.get(live.url + "/v1/meta", headers=headers) for _ in range(3)]
        assert [r.status_code for r in statuses] == [200, 200, 429]
        limited = statuses[-1]
        _validate(contract, "/v1/meta", 429, limited.json())
        assert limited.json()["error"]["code"] == "RATE_LIMITED"
        assert int(limited.headers["retry-after"]) >= 1
        assert limited.headers["access-control-allow-origin"] == "http://localhost:5173"
        assert "retry-after" in limited.headers["access-control-expose-headers"].lower()
        # Health checks are not rate limited.
        assert httpx.get(live.url + "/readyz").status_code == 200


class _ExplodingStore:
    snapshot_id = "snap_fixture_20261003"
    data_mode = "synthetic_fixture"

    def meta(self):
        raise RuntimeError("secret-dsn postgres://user:pw@host/db SELECT * FROM atlas.x")

    def context(self, _id):
        raise NotFound(_id)


@pytest.mark.master("T22", boundary="offline-fixture")
def test_unexpected_failure_is_opaque_500(contract):
    app = create_app(load_settings(FIXTURE_ENV), store=_ExplodingStore())
    with LiveServer(app) as live:
        response = httpx.get(live.url + "/v1/meta", headers={"Origin": "http://localhost:5173"})
    assert response.status_code == 500
    body = response.json()
    _validate(contract, "/v1/meta", 500, body)
    text = response.text
    assert "secret" not in text and "SELECT" not in text and "Traceback" not in text
    assert body["error"]["request_id"] == response.headers["x-request-id"]
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


@pytest.mark.master("T38", boundary="offline-fixture")
@pytest.mark.parametrize("env", [
    {},
    {"ATLAS_DATA_MODE": "fixtures"},
    {"ATLAS_DATA_MODE": "real"},
    {"ATLAS_DATA_MODE": "real", "ATLAS_SNAPSHOT_ID": "snap_" + "0" * 64, "ATLAS_DATABASE_URL": "postgresql://x@127.0.0.1:1/db"},
    {"ATLAS_DATA_MODE": "synthetic_fixture", "OPENAI_API_KEY": "sk-should-not-be-here"},
    {"ATLAS_DATA_MODE": "synthetic_fixture", "ATLAS_PUBLISH_DATABASE_URL": "postgresql://writer@x/db"},
])
def test_missing_or_invalid_configuration_fails_readiness(contract, env):
    app = create_app(load_settings(env))
    with LiveServer(app) as live:
        ready = httpx.get(live.url + "/readyz")
        live_check = httpx.get(live.url + "/healthz")
        product = httpx.get(live.url + "/v1/meta")
    assert ready.status_code == 503 and ready.json()["status"] == "not_ready"
    assert ready.json()["snapshot_id"] is None
    assert live_check.status_code == 200 and live_check.json()["status"] == "ok"
    assert product.status_code == 503
    body = product.json()
    _validate(contract, "/v1/meta", 503, body)
    assert body["error"]["code"] == "SNAPSHOT_UNAVAILABLE" and body["snapshot_id"] is None
    assert "sk-should" not in product.text and "writer" not in product.text


@pytest.mark.master("T37", boundary="offline-fixture")
def test_cors_preflight_and_origin_rules(server):
    pre = httpx.options(server.url + "/v1/meta", headers={
        "Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"})
    assert pre.status_code == 200
    assert pre.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "access-control-allow-credentials" not in pre.headers
    foreign = httpx.get(server.url + "/v1/meta", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in foreign.headers
    post = httpx.post(server.url + "/v1/meta")
    assert post.status_code == 405 and post.json()["error"]["code"] == "METHOD_NOT_ALLOWED"


@pytest.mark.master("T37", boundary="offline-fixture")
@pytest.mark.parametrize("raw", ['"http://a.com"', '["*"]', '["https://a.com/"]', '["https://a.com/path"]', "not json"])
def test_invalid_origin_configuration_is_rejected(raw):
    settings = load_settings({**FIXTURE_ENV, "ATLAS_ALLOWED_ORIGINS": raw})
    assert settings.config_errors


@pytest.mark.master("T22", boundary="offline-fixture")
@pytest.mark.master("T37", boundary="offline-fixture")
def test_openapi_route_serves_committed_contract(server):
    response = httpx.get(server.url + "/openapi.json")
    assert response.status_code == 200
    assert response.content == (ROOT / "contracts" / "openapi.json").read_bytes()


def _normalize(schema: dict, defs: dict, contract: bool) -> object:
    """Reduce a JSON schema to (type, required, properties, enum/const) for drift comparison."""
    if "$ref" in schema:
        name = schema["$ref"].split("/")[-1]
        return ("ref", name)
    for key in ("anyOf", "oneOf"):
        if key in schema:
            return ("any", tuple(sorted(str(_normalize(s, defs, contract)) for s in schema[key])))
    if "const" in schema:
        return ("const", json.dumps(schema["const"]))
    if "enum" in schema:
        return ("enum", tuple(sorted(schema["enum"])))
    typ = schema.get("type")
    if typ == "array":
        return ("array", _normalize(schema.get("items", {}), defs, contract))
    if typ == "object":
        props = schema.get("properties", {})
        return ("object", tuple(sorted(schema.get("required", []))),
                tuple(sorted((k, str(_normalize(v, defs, contract))) for k, v in props.items())),
                schema.get("additionalProperties", True))
    bounds = tuple(sorted((k, schema[k]) for k in ("minimum", "maximum", "minLength", "maxLength") if k in schema))
    return (typ, bounds)


@pytest.mark.master("T22", boundary="offline-fixture")
@pytest.mark.master("T37", boundary="offline-fixture")
def test_dto_models_have_no_drift_from_contract(contract):
    """Generated DTO schemas match the committed contract field-for-field."""
    names = {}
    for name, model in dto.CONTRACT_SCHEMA_MODELS.items():
        generated = model.model_json_schema(ref_template="#/components/schemas/{model}", mode="serialization")
        defs = generated.pop("$defs", {})
        names[name] = (generated, defs)
    contract_schemas = contract["components"]["schemas"]
    assert set(names) == set(contract_schemas)
    alias = {"SourceVersion", "Counts", "Capabilities", "PairIds", "PairCounts", "PairCoverage", "GraphNode",
             "GraphLink", "SourceMetadata", "RecordField", "ReviewSummary", "Badge", "Parameter", "ErrorBody", "_Envelope"}

    def inline(schema, defs):
        # Replace refs to helper models (inline objects in the contract) by their definitions.
        if isinstance(schema, dict):
            if "$ref" in schema and schema["$ref"].split("/")[-1] in alias:
                return inline(defs[schema["$ref"].split("/")[-1]], defs)
            return {k: inline(v, defs) for k, v in schema.items() if k not in ("title", "description", "default")}
        if isinstance(schema, list):
            return [inline(v, defs) for v in schema]
        return schema

    drift = []
    for name, (generated, defs) in names.items():
        mine = _normalize(inline(generated, defs), defs, False)
        theirs = _normalize(inline(contract_schemas[name], {}), {}, True)
        if mine != theirs:
            drift.append(name)
    assert not drift, drift
