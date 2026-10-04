"""Transactional snapshot publisher (pipeline only) and read-only reader (API).

The publisher computes nothing new: it inserts an already validated canonical
package in one transaction (deferred foreign keys checked at commit) and reads
it back before committing. Re-publishing an identical snapshot is a no-op that
keeps the original rows; a stored package whose content differs under the same
ID is reported as corruption. A failure rolls back and leaves every earlier
snapshot untouched. The reader uses a read-only transaction and recomputes the
content hash of the pinned snapshot before the API reports readiness.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from atlas.assembly.package import PACKAGE_FORMAT, TABLES, dotted, hash_projection, load_package, snapshot_id_for

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


class PublishError(RuntimeError):
    pass


@dataclass(frozen=True)
class PublishResult:
    snapshot_id: str
    created: bool
    record_counts: dict[str, int]


def _connect(dsn: str, **kwargs):
    import psycopg

    return psycopg.connect(dsn, connect_timeout=10, **kwargs)


def apply_migrations(admin_dsn: str, directory: Path = MIGRATIONS_DIR) -> list[str]:
    applied: list[str] = []
    with _connect(admin_dsn, autocommit=True) as conn:
        for path in sorted(directory.glob("*.sql")):
            conn.execute(path.read_text(encoding="utf-8"))
            applied.append(path.stem)
    return applied


def _rows(content: dict[str, Any], table: str) -> list[tuple]:
    key, fks = TABLES[table]
    return [(dotted(row, key), *[dotted(row, path) for path in fks.values()], json.dumps(row, ensure_ascii=False))
            for row in content[table]]


def publish(publisher_dsn: str, package: dict[str, Any]) -> PublishResult:
    load_package(package)  # full validation before any write
    content = package["content"]
    snapshot_id = package["snapshot_id"]
    counts = {table: len(content[table]) for table in TABLES}
    with _connect(publisher_dsn) as conn:
        with conn.transaction():
            existing = conn.execute("SELECT content_sha256 FROM atlas.snapshots WHERE snapshot_id = %s",
                                    (snapshot_id,)).fetchone()
            if existing is not None:
                stored = read_package(conn, snapshot_id)
                if (snapshot_id_for(stored["content"]) != snapshot_id
                        or hash_projection(stored["content"]) != hash_projection(content)):
                    raise PublishError(f"integrity failure: stored rows for {snapshot_id} do not match the package")
                return PublishResult(snapshot_id, False, counts)  # no-op: original rows and metadata kept
            conn.execute("SET CONSTRAINTS ALL DEFERRED")
            conn.execute(
                "INSERT INTO atlas.snapshots (snapshot_id, content_sha256, package_format, record_counts, release) "
                "VALUES (%s, %s, %s, %s, %s)",
                (snapshot_id, snapshot_id.removeprefix("snap_"), PACKAGE_FORMAT, json.dumps(counts),
                 json.dumps(content["release"], ensure_ascii=False)))
            for table, (_, fks) in TABLES.items():
                columns = ["snapshot_id", "id", *fks.keys(), "payload"]
                placeholders = ", ".join(["%s"] * len(columns))
                with conn.cursor() as cur:
                    cur.executemany(
                        f"INSERT INTO atlas.{table} ({', '.join(columns)}) VALUES ({placeholders})",
                        [(snapshot_id, *row) for row in _rows(content, table)])
            readback = read_package(conn, snapshot_id)
            if readback["content"] != content:
                raise PublishError("read-back verification failed; rolling back")
    return PublishResult(snapshot_id, True, counts)


def read_package(conn, snapshot_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT package_format, release, record_counts FROM atlas.snapshots WHERE snapshot_id = %s",
                       (snapshot_id,)).fetchone()
    if row is None:
        raise LookupError(f"snapshot {snapshot_id} is not published")
    package_format, release, counts = row
    content: dict[str, Any] = {"release": release}
    for table, (key, _) in TABLES.items():
        rows = conn.execute(f"SELECT payload FROM atlas.{table} WHERE snapshot_id = %s ORDER BY id",
                            (snapshot_id,)).fetchall()
        content[table] = sorted((r[0] for r in rows), key=lambda r: dotted(r, key))
        if len(content[table]) != counts.get(table):
            raise LookupError(f"snapshot {snapshot_id} is incomplete: {table} has {len(content[table])} rows, "
                              f"expected {counts.get(table)}")
    return {"format": package_format, "snapshot_id": snapshot_id, "content": content}


def load_snapshot_readonly(dsn: str, snapshot_id: str) -> dict[str, Any]:
    """API reader: read-only transaction, then full package validation by the caller."""
    with _connect(dsn) as conn:
        conn.read_only = True
        with conn.transaction():
            return read_package(conn, snapshot_id)
