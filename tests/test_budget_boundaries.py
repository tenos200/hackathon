"""Budget/resume, reproducibility, real-mode HTTP and deployment boundaries."""

from __future__ import annotations

import ast
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from atlas.api.main import create_app
from atlas.api.settings import load_settings
from atlas.assembly.assemble import assemble, write_package
from atlas.budget import BudgetExceeded, BudgetLedger
from atlas.cli import assembly_inputs
from atlas.extraction.model_client import (
    MissingRecording, ModelRequest, OpenAIClient, PaidCallRefused, RecordedClient, ResponseCache, make_client,
)

from .conftest import ROOT, LiveServer, endpoint_schema, schema_validator
from .synthetic_world import build_full_world, run_literature

PRICED = {"pricing": {"m": {"input_usd_per_1m": 1.0, "output_usd_per_1m": 4.0, "verified_by": "team",
                            "verified_at": "2026-10-03"}}}


def _request(n: int = 0) -> ModelRequest:
    return ModelRequest(purpose="extraction", model="m", prompt_version="p", schema_name="s", schema={},
                        instructions="i" * 30, input_text=f"text {n}" * 30, settings={}, max_output_tokens=1000,
                        content_hash=f"hash{n}")


class FakeResponses:
    def __init__(self, fail_on: set[int] = frozenset(), input_tokens: int = 270, output_tokens: int = 1000):
        self.calls = []
        self.fail_on = fail_on
        self.usage = SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) in self.fail_on:
            raise TimeoutError("connection reset after send")
        return SimpleNamespace(id=f"resp_{len(self.calls)}", model="m", output_text='{"claims": []}',
                               usage=self.usage)


def _ledger(tmp_path, shared=1.0, provider=1.0, command=1.0):
    return BudgetLedger(tmp_path / "ledger.jsonl", {"shared_cap_usd": shared, "providers": {"openai": {"cap_usd": provider}}},
                        command_cap_usd=command)


@pytest.mark.master("T24", boundary="offline-unit")
def test_paid_calls_need_key_verified_pricing_and_positive_budget(tmp_path):
    cache = ResponseCache(tmp_path / "model")
    assert isinstance(make_client(offline=False, api_key=None, ledger=None, cache=cache, models_config=PRICED), RecordedClient)
    with pytest.raises(MissingRecording):
        RecordedClient(cache).call(_request())
    sdk = SimpleNamespace(responses=FakeResponses())
    zero = OpenAIClient("sk-test", _ledger(tmp_path, shared=0), cache, PRICED, sdk_client=sdk)
    with pytest.raises(BudgetExceeded, match="no positive configured budget"):
        zero.call(_request())
    unverified = {"pricing": {"m": {"input_usd_per_1m": 1.0, "output_usd_per_1m": 4.0, "verified_by": None, "verified_at": None}}}
    with pytest.raises(PaidCallRefused, match="not been verified"):
        OpenAIClient("sk-test", _ledger(tmp_path), cache, unverified, sdk_client=sdk).call(_request())
    assert sdk.responses.calls == []


@pytest.mark.master("T24", boundary="offline-unit")
def test_shared_ledger_stops_new_calls_and_resume_reuses_completed_ones(tmp_path):
    cache = ResponseCache(tmp_path / "model")
    sdk = SimpleNamespace(responses=FakeResponses())
    # Each estimate ~ (~120 tokens * $1 + 1000 * $4)/1e6 ~= $0.0041; cap allows two reservations.
    extraction = OpenAIClient("sk-test", _ledger(tmp_path, shared=0.009, provider=0.009, command=0.009), cache,
                              PRICED, sdk_client=sdk)
    extraction.call(_request(1))
    extraction.call(_request(2))
    with pytest.raises(BudgetExceeded):
        extraction.call(_request(3))
    # A separate stage sharing the ledger cannot spend the cap again.
    checking = OpenAIClient("sk-test", _ledger(tmp_path, shared=0.009, provider=0.009, command=1.0), cache,
                            PRICED, sdk_client=sdk)
    with pytest.raises(BudgetExceeded, match="shared cap"):
        checking.call(_request(4))
    # Resume: completed responses come from the cache without new spending.
    before = len(sdk.responses.calls)
    assert extraction.call(_request(1)).recorded and extraction.call(_request(2)).recorded
    assert len(sdk.responses.calls) == before
    request = sdk.responses.calls[0]
    assert request["text"]["format"]["type"] == "json_schema" and request["text"]["format"]["strict"] is True
    assert request["store"] is False


@pytest.mark.master("T24", boundary="offline-unit")
def test_uncertain_outcomes_stay_charged(tmp_path):
    cache = ResponseCache(tmp_path / "model")
    sdk = SimpleNamespace(responses=FakeResponses(fail_on={1}))
    ledger = _ledger(tmp_path, shared=0.006, provider=0.006, command=0.006)
    client = OpenAIClient("sk-test", ledger, cache, PRICED, sdk_client=sdk)
    with pytest.raises(TimeoutError):
        client.call(_request(1))
    assert '"content_hash":"hash1"' in (tmp_path / "model" / "uncertain.jsonl").read_text()
    with pytest.raises(BudgetExceeded):  # the uncertain attempt is not assumed free
        client.call(_request(1))
    assert ledger.summary()["providers"]["openai"]["requests"] == 1


@pytest.mark.master("T24", boundary="offline-unit")
@pytest.mark.master("T19", boundary="offline-unit")
def test_interrupted_literature_run_replays_recorded_responses(tmp_path):
    world, package, _ = build_full_world(tmp_path / "ws")
    calls_first = world.extraction_client.calls
    assert calls_first == 3  # one call per literature document
    # Re-run the stage: every response is served from the cache, and content is identical.
    run_literature(world, extraction_script=lambda r: pytest.fail("a completed call was requested again"),
                   check_script=lambda r: pytest.fail("a completed check was requested again"))
    again, _ = assemble(assembly_inputs(world.ws))
    assert again["snapshot_id"] == package["snapshot_id"]


@pytest.mark.master("T19", boundary="offline-unit")
def test_fresh_rebuild_from_cached_inputs_reproduces_the_content_hash(tmp_path):
    _, first, _ = build_full_world(tmp_path / "a")
    _, second, _ = build_full_world(tmp_path / "b")
    assert first["snapshot_id"] == second["snapshot_id"]
    from atlas.assembly.package import hash_projection
    from atlas.hashing import content_sha256
    assert first["snapshot_id"] == "snap_" + content_sha256(hash_projection(first["content"]))
    # Operational timestamps are served but do not change the snapshot identity.
    assert any(c["completion"] == "failed" for c in first["content"]["coverage"])


@pytest.fixture
def real_server(tmp_path):
    world, package, _ = build_full_world(tmp_path / "ws")
    path = write_package(package, world.ws.snapshots)
    env = {"ATLAS_DATA_MODE": "real", "ATLAS_SNAPSHOT_ID": package["snapshot_id"], "ATLAS_SNAPSHOT_BACKEND": "file",
           "ATLAS_SNAPSHOT_FILE": path, "ATLAS_ALLOWED_ORIGINS": '["http://localhost:5173"]'}
    with LiveServer(create_app(load_settings(env))) as live:
        yield live, package


@pytest.mark.master("T20", boundary="offline-unit")
@pytest.mark.master("T22", boundary="offline-unit")
def test_real_mode_http_routes_validate_and_stay_pinned(real_server, contract):
    live, package = real_server
    snapshot = package["snapshot_id"]
    assert httpx.get(live.url + "/readyz").json() == {"status": "ready", "contract_version": "1.0.0",
                                                      "snapshot_id": snapshot, "data_mode": "real"}
    routes = ["/v1/meta", "/v1/search?q=synthetic%20disorder%20A"]
    for ctx in [c["record"]["id"] for c in package["content"]["contexts"]]:
        routes += [f"/v1/contexts/{ctx}", f"/v1/contexts/{ctx}/connections", f"/v1/contexts/{ctx}/actions"]
    routes += [f"/v1/assertions/{a['record']['id']}" for a in package["content"]["assertions"]]
    routes += [f"/v1/calculations/{c['id']}" for c in package["content"]["calculations"]]
    for route in routes:
        response = httpx.get(live.url + route)
        assert response.status_code == 200, route
        body = response.json()
        errors = list(schema_validator(contract, endpoint_schema(contract, route.split("?")[0], 200)).iter_errors(body))
        assert not errors, (route, errors[:2])
        assert body["snapshot_id"] == snapshot and body["data_mode"] == "real" and body["warnings"] == [] or route.startswith("/v1/search")
    meta = httpx.get(live.url + "/v1/meta").json()["data"]
    assert meta["capabilities"] == {"live_ai": False, "request_snapshot_pin": False, "graph": True}
    assert meta["content_sha256"] == snapshot.removeprefix("snap_")
    for route, status in (("/v1/contexts/ctx:nope", 404), ("/v1/assertions/assert:nope", 404), ("/v1/search?q=%20", 422)):
        response = httpx.get(live.url + route)
        assert response.status_code == status and response.json()["snapshot_id"] == snapshot


@pytest.mark.master("T38", boundary="offline-unit")
def test_real_mode_refuses_a_tampered_or_mismatched_snapshot(tmp_path):
    world, package, _ = build_full_world(tmp_path / "ws")
    path = Path(write_package(package, world.ws.snapshots))
    tampered = json.loads(path.read_text())
    tampered["content"]["release"]["title"] = "tampered"
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(tampered))
    for env in (
        {"ATLAS_SNAPSHOT_ID": package["snapshot_id"], "ATLAS_SNAPSHOT_FILE": str(bad)},
        {"ATLAS_SNAPSHOT_ID": "snap_" + "1" * 64, "ATLAS_SNAPSHOT_FILE": str(path)},
    ):
        app = create_app(load_settings({"ATLAS_DATA_MODE": "real", "ATLAS_SNAPSHOT_BACKEND": "file", **env}))
        with LiveServer(app) as live:
            assert httpx.get(live.url + "/readyz").status_code == 503
            assert httpx.get(live.url + "/v1/meta").status_code == 503


API_MODULES = sorted((ROOT / "atlas" / "api").glob("*.py"))
FORBIDDEN_IN_API = ("openai", "httpx", "atlas.extraction", "atlas.sources.fetch", "atlas.pipeline", "atlas.budget",
                    "atlas.review.workflow", "atlas.cli")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _closure(module: str, seen: set[str]) -> set[str]:
    if module in seen or not module.startswith("atlas"):
        return seen
    seen.add(module)
    path = ROOT / (module.replace(".", "/") + ".py")
    if not path.exists():
        path = ROOT / module.replace(".", "/") / "__init__.py"
    if path.exists():
        for name in _imports(path):
            if name.startswith("atlas"):
                _closure(name, seen)
            else:
                seen.add(name)
    return seen


@pytest.mark.master("T21", boundary="static-config")
@pytest.mark.master("T38", boundary="static-config")
def test_api_cannot_reach_pipeline_model_or_fetch_code():
    reachable = _closure("atlas.api.main", set())
    bad = [m for m in reachable if any(m == f or m.startswith(f + ".") for f in FORBIDDEN_IN_API)]
    assert not bad, bad
    runtime = (ROOT / "requirements.txt").read_text()
    assert "openai" not in runtime and "httpx" not in runtime.split("uvicorn")[0].replace("httpx2", "")


@pytest.mark.master("T38", boundary="static-config")
def test_render_blueprint_and_env_example_carry_no_secrets():
    render = (ROOT / "render.yaml").read_text()
    assert "uvicorn atlas.api.main:app --host 0.0.0.0 --port $PORT" in render
    assert "healthCheckPath: /readyz" in render and "pip install -r requirements.txt" in render
    for secret in ("ATLAS_DATABASE_URL", "ATLAS_SNAPSHOT_ID", "ATLAS_ALLOWED_ORIGINS", "ATLAS_DATA_MODE"):
        block = render.split(f"key: {secret}")[1].split("- key:")[0]
        assert "sync: false" in block and "value:" not in block
    api_block, assistant_block = render.split("name: atlas-assistant")
    assert "OPENAI" not in api_block and "BRIGHTDATA" not in render and "PUBLISH" not in render
    assert "OPENAI_API_KEY:" not in render.replace("ATLAS_AGENT_OPENAI_API_KEY", "")  # no pipeline key anywhere
    key_block = assistant_block.split("key: ATLAS_AGENT_OPENAI_API_KEY")[1].split("- key:")[0]
    assert "sync: false" in key_block and "value:" not in key_block
    assert "uvicorn atlas.agent.main:app" in assistant_block
    example = (ROOT / ".env.example").read_text()
    for line in example.splitlines():
        if "=" in line and not line.startswith("#"):
            value = line.split("=", 1)[1].split("#")[0].strip()
            assert value in ("", "synthetic_fixture", "postgres", "120", "10", '["http://localhost:5173"]'), line


@pytest.mark.master("T38", boundary="static-config")
def test_api_startup_performs_no_ingestion_or_network(tmp_path, monkeypatch):
    """Startup in fixture mode touches only local files; the socket guard would fail any external call."""
    import atlas.sources.fetch as fetch

    monkeypatch.setattr(fetch.Fetcher, "get", lambda *a, **k: pytest.fail("API startup fetched a source"))
    app = create_app(load_settings({"ATLAS_DATA_MODE": "synthetic_fixture"}))
    with LiveServer(app) as live:
        assert httpx.get(live.url + "/readyz").status_code == 200


@pytest.mark.master("T25", boundary="offline-unit")
def test_offline_suite_runs_without_credentials():
    import os
    import socket

    for name in ("OPENAI_API_KEY", "BRIGHTDATA_API_KEY", "ATLAS_PUBLISH_DATABASE_URL", "ATLAS_DATABASE_URL"):
        assert not os.environ.get(name), f"{name} must not be needed by the offline suite"
    with pytest.raises(RuntimeError, match="external network"):
        socket.create_connection(("192.0.2.1", 443), timeout=1)


@pytest.mark.master("T19", boundary="offline-unit")
@pytest.mark.master("T25", boundary="offline-unit")
def test_cli_chain_replays_offline_to_the_same_snapshot(tmp_path):
    import os
    import subprocess
    import sys

    world, package, _ = build_full_world(tmp_path / "ws")
    env = {k: v for k, v in os.environ.items() if k not in ("OPENAI_API_KEY", "BRIGHTDATA_API_KEY")}

    def cli(*args: str) -> dict:
        result = subprocess.run([sys.executable, "-m", "atlas", "--root", str(world.root), *args], cwd=ROOT, env=env,
                                capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, (args, result.stderr)
        return json.loads(result.stdout)

    cli("catalog-freeze")
    assert cli("extract", "--cached-sources", "--offline")["failures"] == []
    assert cli("check", "--cached-sources", "--offline")["unchecked_remain_pending"] == 0
    assert cli("review-export")["queue_items"] == 1  # only the structurally blocked protocol claim
    assembled = cli("assemble", "--offline")
    assert assembled["snapshot_id"] == package["snapshot_id"]
    assert cli("validate", "--snapshot", assembled["snapshot_id"])["valid"] is True
