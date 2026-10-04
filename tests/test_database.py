"""Database boundary tests against a throwaway local PostgreSQL 16 cluster (database-local).

These exercise the real migrations, grants, triggers, publisher and read-only
reader. They are NOT a Supabase test: Supabase-specific settings (exposed
schemas, pooler usernames, TLS, RLS interaction with Supabase roles) still need
checking against the team's actual project.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import httpx
import pytest

from atlas.api.main import create_app
from atlas.api.settings import load_settings
from atlas.storage.postgres import PublishError, apply_migrations, load_snapshot_readonly, publish

from .conftest import ROOT, LiveServer, free_port
from .synthetic_world import build_full_world

PG_BIN = Path(os.environ.get("ATLAS_TEST_PG_BIN", "/usr/lib/postgresql/16/bin"))
pytestmark = pytest.mark.database


def _as_postgres(*cmd: str) -> subprocess.CompletedProcess:
    prefix = ["runuser", "-u", "postgres", "--"] if os.geteuid() == 0 else []
    return subprocess.run([*prefix, *cmd], check=True, capture_output=True, text=True)


@pytest.fixture(scope="module")
def cluster():
    if not (PG_BIN / "initdb").exists():
        pytest.skip("PostgreSQL server binaries are not installed")
    base = Path(tempfile.mkdtemp(prefix="atlas-pg-", dir="/tmp"))
    os.chmod(base, 0o777)
    (base / "pw").write_text("admin-test-password")
    os.chmod(base / "pw", 0o644)
    port = free_port()
    _as_postgres(str(PG_BIN / "initdb"), "-D", str(base / "data"), "-U", "atlas_admin", f"--pwfile={base / 'pw'}",
                 "--auth-local=trust", "--auth-host=scram-sha-256", "-E", "UTF8")
    _as_postgres(str(PG_BIN / "pg_ctl"), "-D", str(base / "data"), "-l", str(base / "log"), "-w", "-o",
                 f"-p {port} -k {base} -c listen_addresses=127.0.0.1", "start")
    admin = f"postgresql://atlas_admin:admin-test-password@127.0.0.1:{port}/postgres"
    import psycopg
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute("CREATE DATABASE atlas_test")
        conn.execute("CREATE ROLE anon NOLOGIN")          # Supabase-style roles, to test revocation
        conn.execute("CREATE ROLE authenticated NOLOGIN")
    dsn = lambda user, pw: f"postgresql://{user}:{pw}@127.0.0.1:{port}/atlas_test"
    info = {"admin": dsn("atlas_admin", "admin-test-password"), "port": port}
    apply_migrations(info["admin"])
    apply_migrations(info["admin"])  # idempotent re-application
    subprocess.run(["psql", info["admin"], "-v", "ON_ERROR_STOP=1", "-v", "api_password=api-test-pw",
                    "-v", "publisher_password=pub-test-pw", "-f", str(ROOT / "scripts" / "create_login_roles.sql")],
                   check=True, capture_output=True, text=True)
    info["api"] = dsn("atlas_api_login", "api-test-pw")
    info["publisher"] = dsn("atlas_publisher_login", "pub-test-pw")
    yield info
    _as_postgres(str(PG_BIN / "pg_ctl"), "-D", str(base / "data"), "-m", "fast", "stop")
    shutil.rmtree(base, ignore_errors=True)


@pytest.fixture(scope="module")
def packages(tmp_path_factory):
    world, package, _ = build_full_world(tmp_path_factory.mktemp("db") / "ws")
    return world, package


def _denied(dsn: str, sql: str) -> str:
    import psycopg
    with psycopg.connect(dsn) as conn:
        try:
            conn.execute(sql)
        except psycopg.Error as exc:
            return str(exc)
    return ""


@pytest.mark.master("T21", boundary="database-local")
@pytest.mark.master("T38", boundary="database-local")
def test_api_role_is_read_only_and_exposed_roles_have_nothing(cluster, packages):
    import psycopg
    _, package = packages
    publish(cluster["publisher"], package)
    with psycopg.connect(cluster["api"]) as conn:
        assert conn.execute("SELECT count(*) FROM atlas.snapshots").fetchone()[0] >= 1
        assert conn.execute("SHOW default_transaction_read_only").fetchone()[0] == "on"
    for sql in ("INSERT INTO atlas.coverage (snapshot_id, id, payload) VALUES ('x','y','{}')",
                "UPDATE atlas.snapshots SET release = '{}'", "DELETE FROM atlas.entities",
                "CREATE TABLE atlas.evil (id int)", "CREATE TABLE public.evil (id int)",
                "SET ROLE atlas_publisher"):
        assert _denied(cluster["api"], sql), sql
    with psycopg.connect(cluster["admin"]) as conn:
        for role in ("anon", "authenticated", "public"):
            usage = conn.execute("SELECT has_schema_privilege(%s, 'atlas', 'USAGE')",
                                 (role if role != "public" else "atlas_admin",)).fetchone()[0]
            if role != "public":
                assert usage is False
        assert conn.execute("SELECT has_table_privilege('anon', 'atlas.assertions', 'SELECT')").fetchone()[0] is False
        assert conn.execute("SELECT has_table_privilege('atlas_api', 'atlas.assertions', 'INSERT')").fetchone()[0] is False


@pytest.mark.master("T18", boundary="database-local")
@pytest.mark.master("T19", boundary="database-local")
def test_publish_is_idempotent_immutable_and_failure_leaves_prior_snapshot_intact(cluster, packages, monkeypatch):
    import psycopg
    _, package = packages
    first = publish(cluster["publisher"], package)
    again = publish(cluster["publisher"], package)
    assert again.created is False and again.snapshot_id == first.snapshot_id
    with psycopg.connect(cluster["admin"]) as conn:
        counts = {t: conn.execute(f"SELECT count(*) FROM atlas.{t} WHERE snapshot_id=%s", (package["snapshot_id"],)).fetchone()[0]
                  for t in ("assertions", "evidence", "calculations")}
    assert counts == {t: len(package["content"][t]) for t in counts}  # no duplicate rows
    # Published rows are immutable even for the publisher (and for the owner via triggers).
    for sql in ("UPDATE atlas.assertions SET payload = '{}'", "DELETE FROM atlas.assertions", "TRUNCATE atlas.coverage"):
        assert _denied(cluster["publisher"], sql), sql
    assert "immutable" in _denied(cluster["admin"], "DELETE FROM atlas.gaps")
    # A package with a dangling reference never starts writing.
    broken = json.loads(json.dumps(package))
    broken["content"]["supports"][0]["evidence_id"] = "ev:missing"
    with pytest.raises(Exception, match="dangling|does not match"):
        publish(cluster["publisher"], broken)
    # A failure inside the transaction (bypassing package validation) rolls back completely.
    import atlas.storage.postgres as storage
    bad = json.loads(json.dumps(package))
    bad["content"]["release"]["title"] = "a different package"
    from atlas.assembly.package import snapshot_id_for
    bad["snapshot_id"] = snapshot_id_for(bad["content"])
    bad["content"]["evidence"][0]["source_id"] = "src:not-published"
    monkeypatch.setattr(storage, "load_package", lambda p: None)
    with pytest.raises(psycopg.Error):
        storage.publish(cluster["publisher"], bad)
    with psycopg.connect(cluster["admin"]) as conn:
        assert conn.execute("SELECT count(*) FROM atlas.snapshots WHERE snapshot_id=%s", (bad["snapshot_id"],)).fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM atlas.evidence WHERE snapshot_id=%s", (bad["snapshot_id"],)).fetchone()[0] == 0
    restored = load_snapshot_readonly(cluster["api"], package["snapshot_id"])
    from atlas.assembly.package import load_package
    load_package(restored, expected_snapshot_id=package["snapshot_id"])  # prior snapshot intact and valid


@pytest.mark.master("T18", boundary="database-local")
def test_same_id_with_different_stored_content_is_reported_as_corruption(cluster, packages):
    _, package = packages
    publish(cluster["publisher"], package)
    import copy
    tampered = copy.deepcopy(package)
    tampered["content"]["release"]["title"] = "tampered under the same ID"
    with pytest.raises(Exception, match="does not match"):
        publish(cluster["publisher"], tampered)


@pytest.mark.master("T38", boundary="database-local")
@pytest.mark.master("T20", boundary="database-local")
def test_api_serves_pinned_snapshot_through_read_only_role(cluster, packages):
    _, package = packages
    publish(cluster["publisher"], package)
    env = {"ATLAS_DATA_MODE": "real", "ATLAS_SNAPSHOT_ID": package["snapshot_id"], "ATLAS_DATABASE_URL": cluster["api"]}
    with LiveServer(create_app(load_settings(env))) as live:
        assert httpx.get(live.url + "/readyz").json()["status"] == "ready"
        meta = httpx.get(live.url + "/v1/meta").json()
        assert meta["snapshot_id"] == package["snapshot_id"] and meta["data_mode"] == "real"
    missing = {**env, "ATLAS_SNAPSHOT_ID": "snap_" + "a" * 64}
    with LiveServer(create_app(load_settings(missing))) as live:
        assert httpx.get(live.url + "/readyz").status_code == 503  # no fallback to another snapshot
        assert httpx.get(live.url + "/v1/meta").json()["error"]["code"] == "SNAPSHOT_UNAVAILABLE"
    unreachable = {**env, "ATLAS_DATABASE_URL": f"postgresql://atlas_api_login:x@127.0.0.1:{free_port()}/atlas_test"}
    with LiveServer(create_app(load_settings(unreachable))) as live:
        assert httpx.get(live.url + "/readyz").status_code == 503
        assert httpx.get(live.url + "/healthz").status_code == 200
