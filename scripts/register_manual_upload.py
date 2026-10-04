"""Register a file captured outside the fetch script (browser save, scraper export).

    python scripts/register_manual_upload.py <file> --source nord --url <page or search URL> \
        --backend brightdata --fetched-at 2026-10-04T10:00:00Z --note "how it was captured"

The file is copied to hackathon-claude-repo/data/manual/<source>/ and a row with its SHA-256 is appended to
hackathon-claude-repo/data/manual_manifest.jsonl, which the fetch script never rewrites. `real-ingest` verifies
these rows exactly like the fetch script's own manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "hackathon-claude-repo" / "data"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("file")
    parser.add_argument("--source", required=True, help="loader name, e.g. nord or web")
    parser.add_argument("--url", required=True, help="the URL the content was captured from")
    parser.add_argument("--backend", required=True, choices=["direct", "brightdata"])
    parser.add_argument("--fetched-at", required=True, help="UTC capture time, e.g. 2026-10-04T10:00:00Z")
    parser.add_argument("--note", default="")
    args = parser.parse_args()
    if not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", args.fetched_at):
        raise SystemExit("--fetched-at must look like 2026-10-04T10:00:00Z")
    src = Path(args.file)
    body = src.read_bytes()
    digest = hashlib.sha256(body).hexdigest()
    rel = Path("manual") / args.source / src.name
    (DATA / rel).parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, DATA / rel)
    manifest = DATA / "manual_manifest.jsonl"
    rows = [json.loads(l) for l in manifest.read_text().splitlines() if l.strip()] if manifest.exists() else []
    rows = [r for r in rows if r["path"] != str(rel)]
    rows.append({"key": f"{args.source}:{src.stem}", "source": args.source, "path": str(rel), "url": args.url,
                 "method": "GET", "status": 200, "ok": True, "sha256": digest, "bytes": len(body),
                 "fetched_at": args.fetched_at, "backend": args.backend,
                 "content_type": "application/json" if src.suffix == ".json" else "text/html",
                 "meta": {"note": args.note}})
    manifest.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
    print(f"registered {rel} sha256={digest}")


if __name__ == "__main__":
    main()
