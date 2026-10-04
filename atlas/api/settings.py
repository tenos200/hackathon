"""API runtime configuration, read once from the environment at startup.

Invalid configuration never falls back to fixtures. It is reported as a
readiness failure so the service stays unready (503) instead of serving the
wrong mode.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

DATA_MODES = ("real", "synthetic_fixture")
SNAPSHOT_BACKENDS = ("postgres", "file")
# Credentials that belong only to the offline pipeline or the assistant service. Their presence in the
# API environment is a deployment-separation failure (master test T38).
PIPELINE_ONLY_ENV = ("OPENAI_API_KEY", "BRIGHTDATA_API_KEY", "ATLAS_PUBLISH_DATABASE_URL", "ATLAS_AGENT_OPENAI_API_KEY")
_ORIGIN_RE = re.compile(r"^https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?$")
_SNAPSHOT_RE = re.compile(r"^snap_[0-9a-f]{64}$")


class ConfigError(ValueError):
    """A configuration problem that must keep the service unready."""


@dataclass(frozen=True)
class Settings:
    data_mode: str | None
    snapshot_id: str | None
    snapshot_backend: str
    database_url: str | None = field(repr=False)
    snapshot_file: str | None
    allowed_origins: tuple[str, ...]
    rate_limit_per_minute: int
    fixture_scenarios: tuple[str, ...]
    config_errors: tuple[str, ...]


def parse_origins(raw: str | None) -> tuple[str, ...]:
    if raw is None or raw.strip() == "":
        return ()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError("ATLAS_ALLOWED_ORIGINS must be a JSON array of origin strings") from exc
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError("ATLAS_ALLOWED_ORIGINS must be a JSON array of origin strings")
    for origin in value:
        if origin == "*" or not _ORIGIN_RE.match(origin):
            raise ConfigError(
                f"Invalid origin {origin!r}: use scheme://host[:port] with no path, trailing slash or wildcard"
            )
    return tuple(value)


def load_settings(env: dict[str, str] | None = None) -> Settings:
    env = dict(os.environ if env is None else env)
    errors: list[str] = []

    data_mode = env.get("ATLAS_DATA_MODE")
    if data_mode not in DATA_MODES:
        errors.append(f"ATLAS_DATA_MODE must be one of {DATA_MODES}; got {data_mode!r}")
        data_mode = None

    backend = env.get("ATLAS_SNAPSHOT_BACKEND", "postgres")
    if backend not in SNAPSHOT_BACKENDS:
        errors.append(f"ATLAS_SNAPSHOT_BACKEND must be one of {SNAPSHOT_BACKENDS}")

    snapshot_id = env.get("ATLAS_SNAPSHOT_ID") or None
    database_url = env.get("ATLAS_DATABASE_URL") or None
    snapshot_file = env.get("ATLAS_SNAPSHOT_FILE") or None
    if data_mode == "real":
        if snapshot_id is None or not _SNAPSHOT_RE.match(snapshot_id):
            errors.append("Real mode requires ATLAS_SNAPSHOT_ID of the form snap_<64 hex>")
        if backend == "postgres" and database_url is None:
            errors.append("Real mode with the postgres backend requires ATLAS_DATABASE_URL (read-only role)")
        if backend == "file" and snapshot_file is None:
            errors.append("Real mode with the file backend requires ATLAS_SNAPSHOT_FILE")

    present = [name for name in PIPELINE_ONLY_ENV if env.get(name)]
    if present:
        errors.append("Pipeline-only credentials are present in the API environment: " + ", ".join(present))

    try:
        origins = parse_origins(env.get("ATLAS_ALLOWED_ORIGINS"))
    except ConfigError as exc:
        errors.append(str(exc))
        origins = ()

    try:
        rate = int(env.get("ATLAS_RATE_LIMIT_PER_MINUTE", "120"))
        if rate < 0:
            raise ValueError
    except ValueError:
        errors.append("ATLAS_RATE_LIMIT_PER_MINUTE must be a non-negative integer (0 disables)")
        rate = 0

    scenarios = tuple(s.strip() for s in env.get("ATLAS_FIXTURE_SCENARIOS", "").split(",") if s.strip())

    return Settings(
        data_mode=data_mode,
        snapshot_id=snapshot_id,
        snapshot_backend=backend,
        database_url=database_url,
        snapshot_file=snapshot_file,
        allowed_origins=origins,
        rate_limit_per_minute=rate,
        fixture_scenarios=scenarios,
        config_errors=tuple(errors),
    )
