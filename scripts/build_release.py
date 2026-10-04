"""Build a servable snapshot from the uploaded data in one command.

    python scripts/build_release.py --reviewer "<name>" --reason "<what this person decided>"

Running it IS the named person's decision: every pending mapping, context
definition and assertion that passes the structural checks is recorded as
accepted under --reviewer with --reason (source-fidelity, never expert
validation), bound to the current content hashes. Records with structural
failures are still rejected by the importer. config/release.json must already
carry a human-set title and published_at (its limitations should disclose a
blanket acceptance). The validated package is copied to snapshots/ so it can be
committed and served by the file backend.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def atlas(*args: str) -> dict:
    result = subprocess.run([sys.executable, "-m", "atlas", *args], cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        sys.stderr.write(result.stdout + result.stderr)
        raise SystemExit(f"atlas {args[0]} failed")
    return json.loads(result.stdout) if result.stdout.strip().startswith("{") else {}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--skip-ingest", action="store_true")
    args = parser.parse_args()
    if not args.reviewer.strip() or not args.reason.strip():
        raise SystemExit("--reviewer and --reason must name the person deciding and why")
    if not args.skip_ingest:
        atlas("real-ingest")
    for target_type in ("mapping", "assertion", "context"):
        out = atlas("review-batch", "--target-type", target_type, "--reviewer", args.reviewer,
                    "--reason", args.reason, "--apply")
        print(f"{target_type}: recorded {out.get('recorded')} rejected {out.get('rejected')}")
    built = atlas("assemble", "--offline")
    snapshot_id = built["snapshot_id"]
    checked = atlas("validate", "--snapshot", snapshot_id)
    target = ROOT / "snapshots" / f"{snapshot_id}.json"
    target.parent.mkdir(exist_ok=True)
    # Earlier packages are kept so the deployed service keeps working until ATLAS_SNAPSHOT_ID is switched;
    # delete them once Render serves the new one.
    shutil.copyfile(built["package"], target)
    print(json.dumps({"snapshot_id": snapshot_id, "published": built["published"],
                      "withheld_count": built["withheld_count"],
                      "route_payloads_validated": checked["route_payloads_validated"],
                      "package": str(target.relative_to(ROOT)), "bytes": target.stat().st_size,
                      "render_env": {"ATLAS_DATA_MODE": "real", "ATLAS_SNAPSHOT_ID": snapshot_id,
                                     "ATLAS_SNAPSHOT_BACKEND": "file",
                                     "ATLAS_SNAPSHOT_FILE": str(target.relative_to(ROOT))}}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
