"""Real-browser CORS probe of the API (headless Chromium via Node Playwright).

Starts the API in explicit synthetic_fixture mode allowing only http://localhost:5173,
serves a probe page from that origin and from a disallowed one (5174), and records
what the browser could read. This is a local browser check of CORS/error behavior,
NOT the deployed Lovable journey (T23) and not a test of the real snapshot.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def wait(port: int) -> None:
    deadline = time.time() + 15
    while time.time() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.1)
    raise RuntimeError(f"port {port} did not open")


def main() -> int:
    env = {**os.environ, "ATLAS_DATA_MODE": "synthetic_fixture", "ATLAS_ALLOWED_ORIGINS": '["http://localhost:5173"]'}
    for name in ("OPENAI_API_KEY", "BRIGHTDATA_API_KEY", "ATLAS_PUBLISH_DATABASE_URL"):
        env.pop(name, None)
    procs = [
        subprocess.Popen([sys.executable, "-m", "uvicorn", "atlas.api.main:app", "--port", "8799"], cwd=ROOT, env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL),
        subprocess.Popen([sys.executable, "-m", "http.server", "5173", "--bind", "127.0.0.1"],
                         cwd=ROOT / "scripts" / "browser", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL),
        subprocess.Popen([sys.executable, "-m", "http.server", "5174", "--bind", "127.0.0.1"],
                         cwd=ROOT / "scripts" / "browser", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL),
    ]
    try:
        for port in (8799, 5173, 5174):
            wait(port)
        node_env = {**os.environ, "NODE_PATH": subprocess.run(["npm", "root", "-g"], capture_output=True, text=True).stdout.strip()}
        result = subprocess.run(["node", "scripts/browser/probe.cjs", "http://localhost:5173/page.html",
                                 "http://localhost:5174/page.html", "http://127.0.0.1:8799/v1"],
                                cwd=ROOT, env=node_env, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            print(result.stderr, file=sys.stderr)
            return 1
        observed = json.loads(result.stdout.strip().splitlines()[-1])
    finally:
        for p in procs:
            p.terminate()
    allowed, blocked = observed["allowed_origin"], observed["disallowed_origin"]
    checks = {
        "allowed origin reads meta": allowed["meta"]["ok"] and allowed["meta"]["status"] == 200,
        "allowed origin reads 404 envelope": allowed["missing"].get("code") == "NOT_FOUND" and allowed["missing"]["status"] == 404,
        "allowed origin reads 422 envelope": allowed["invalid"].get("code") == "INVALID_REQUEST",
        "X-Request-ID exposed to browser": bool(allowed["meta"].get("requestIdHeader")),
        "encoded colon ID resolves": allowed["context"].get("status") == 200,
        "server mode visible as synthetic_fixture": allowed["meta"].get("mode") == "synthetic_fixture",
        "disallowed origin cannot read": all(not v["ok"] for v in blocked.values()),
    }
    report = {"boundary": "browser-local (headless Chromium, local fixture API; not Lovable, not deployed)",
              "checks": checks, "observed": observed,
              "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports" / "browser_cors_check.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(checks, indent=2))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
