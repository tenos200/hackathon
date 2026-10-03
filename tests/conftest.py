"""Shared test fixtures and the master-test (T01-T38) result recorder.

Tests tag themselves with `@pytest.mark.master("T12", boundary="offline-unit")`.
After the run, `reports/master_tests.json` records the actual outcome of each
tagged test grouped by master ID and boundary, so the completion report is
generated from executed tests rather than written by hand. Boundaries:

- offline-fixture: shared contract fixtures over real HTTP (interface only)
- offline-unit: synthetic semantic/source-format fixtures, recorded responses
- database-local: a throwaway local PostgreSQL 16 cluster (not Supabase)
- static-config: deployment/config files inspected without hosting access
"""

from __future__ import annotations

import json
import socket
import threading
import time
from collections import defaultdict
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports" / "master_tests.json"
_results: dict[str, list[dict]] = defaultdict(list)


def pytest_configure(config):
    config.addinivalue_line("markers", "master(test_id, boundary): master acceptance test mapping")
    config.addinivalue_line("markers", "database: requires a local PostgreSQL binary (initdb/pg_ctl)")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when == "call" or (report.when == "setup" and report.outcome != "passed"):
        for mark in item.iter_markers("master"):
            _results[mark.args[0]].append({
                "test": item.nodeid,
                "boundary": mark.kwargs.get("boundary", "offline-unit"),
                "outcome": report.outcome,
            })


def pytest_sessionfinish(session, exitstatus):
    if not _results:
        return
    REPORT.parent.mkdir(exist_ok=True)
    previous = json.loads(REPORT.read_text()) if REPORT.exists() else {}
    merged = previous.get("results", {})
    for test_id, rows in _results.items():
        kept = [r for r in merged.get(test_id, []) if r["test"] not in {x["test"] for x in rows}]
        merged[test_id] = sorted(kept + rows, key=lambda r: r["test"])
    REPORT.write_text(json.dumps({"results": dict(sorted(merged.items()))}, indent=2) + "\n")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LiveServer:
    """Runs a real uvicorn HTTP server in a thread so tests exercise actual HTTP."""

    def __init__(self, app) -> None:
        import uvicorn

        self.port = free_port()
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self) -> "LiveServer":
        self.thread.start()
        deadline = time.time() + 10
        while not self.server.started:
            if time.time() > deadline:
                raise RuntimeError("uvicorn did not start")
            time.sleep(0.02)
        return self

    def __exit__(self, *exc) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)


@pytest.fixture(scope="session")
def contract() -> dict:
    return json.loads((ROOT / "contracts" / "openapi.json").read_text())


@pytest.fixture(scope="session")
def fixture_bundle() -> dict:
    return json.loads((ROOT / "contracts" / "fixtures.json").read_text())


def schema_validator(contract: dict, schema: dict):
    from jsonschema import Draft202012Validator

    return Draft202012Validator({**schema, "components": contract["components"]})


def endpoint_schema(contract: dict, path: str, status: int) -> dict:
    template = path
    if path.startswith("/v1/contexts/"):
        suffix = "/connections" if path.endswith("/connections") else "/actions" if path.endswith("/actions") else ""
        template = "/v1/contexts/{id}" + suffix
    elif path.startswith("/v1/assertions/"):
        template = "/v1/assertions/{id}"
    elif path.startswith("/v1/calculations/"):
        template = "/v1/calculations/{id}"
    return contract["paths"][template]["get"]["responses"][str(status)]["content"]["application/json"]["schema"]


@pytest.fixture(autouse=True, scope="session")
def _offline_network_guard():
    """Offline tests must never reach an external host (T25): only loopback connections are allowed."""
    import socket

    real_connect = socket.socket.connect

    def guarded(self, address):
        if isinstance(address, tuple) and address[0] not in ("127.0.0.1", "localhost", "::1"):
            raise RuntimeError(f"offline test attempted external network access to {address[0]}")
        return real_connect(self, address)

    socket.socket.connect = guarded
    yield
    socket.socket.connect = real_connect


@pytest.fixture
def world(tmp_path):
    from tests.synthetic_world import build_full_world

    built, package, report = build_full_world(tmp_path / "ws")
    return built, package, report


def real_store_for(package):
    from atlas.api.real_store import RealStore
    from atlas.assembly.package import load_package

    return RealStore(package["snapshot_id"], load_package(package))
