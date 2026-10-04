"""Local non-cold latency of the API (fixture mode and a synthetic real-mode package).

Measures warm requests against a local uvicorn process on this machine. It says
nothing about Render cold starts or network latency; those must be measured on
the actual deployment.
"""

from __future__ import annotations

import json
import os
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def measure(env: dict[str, str], routes: list[str], rounds: int = 30) -> dict:
    port = _port()
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "atlas.api.main:app", "--port", str(port),
                             "--log-level", "warning"], cwd=ROOT, env={**os.environ, "ATLAS_RATE_LIMIT_PER_MINUTE": "0", **env},
                            stderr=subprocess.DEVNULL)
    try:
        started = time.perf_counter()
        while True:
            try:
                if httpx.get(f"http://127.0.0.1:{port}/readyz").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if time.perf_counter() - started > 30:
                raise RuntimeError("not ready")
            time.sleep(0.05)
        startup_to_ready = time.perf_counter() - started
        timings: list[float] = []
        with httpx.Client(base_url=f"http://127.0.0.1:{port}") as client:
            for route in routes:
                client.get(route)  # warm
            for _ in range(rounds):
                for route in routes:
                    t = time.perf_counter()
                    assert client.get(route).status_code == 200, route
                    timings.append((time.perf_counter() - t) * 1000)
        timings.sort()
        return {"requests": len(timings), "p50_ms": round(statistics.median(timings), 2),
                "p95_ms": round(timings[int(len(timings) * 0.95) - 1], 2), "max_ms": round(timings[-1], 2),
                "local_process_start_to_ready_s": round(startup_to_ready, 2)}
    finally:
        proc.terminate()


def main() -> None:
    from tests.synthetic_world import build_full_world
    from atlas.assembly.assemble import write_package

    fixture_routes = ["/v1/meta", "/v1/search?q=alpha", "/v1/contexts/ctx:alpha-loss",
                      "/v1/contexts/ctx:alpha-loss/connections", "/v1/contexts/ctx:alpha-loss/actions",
                      "/v1/assertions/assert:alpha-loss-A", "/v1/calculations/calc:alpha-beta-subgroup"]
    fixture = measure({"ATLAS_DATA_MODE": "synthetic_fixture"}, fixture_routes)
    with tempfile.TemporaryDirectory() as tmp:
        world, package, _ = build_full_world(Path(tmp) / "ws")
        path = write_package(package, world.ws.snapshots)
        assertion = package["content"]["assertions"][0]["record"]["id"]
        calc = package["content"]["calculations"][0]["id"]
        real_routes = ["/v1/meta", "/v1/search?q=synthetic", "/v1/contexts/ctx:syn-a-loss",
                       "/v1/contexts/ctx:syn-a-loss/connections", "/v1/contexts/ctx:syn-a-loss/actions",
                       f"/v1/assertions/{assertion}", f"/v1/calculations/{calc}"]
        real = measure({"ATLAS_DATA_MODE": "real", "ATLAS_SNAPSHOT_ID": package["snapshot_id"],
                        "ATLAS_SNAPSHOT_BACKEND": "file", "ATLAS_SNAPSHOT_FILE": path}, real_routes)
    report = {"boundary": "local machine, warm requests, single client; NOT Render, NOT cold start",
              "target": "p95 < 500 ms non-cold on the demo snapshot (master plan section 10)",
              "synthetic_fixture_mode": fixture, "real_mode_synthetic_package": real,
              "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports" / "latency_local.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
