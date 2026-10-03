"""Canonical JSON and content hashing shared by every stage.

One canonical projection is used both for content hashes and for idempotency
comparisons: sorted object keys, no insignificant whitespace, UTF-8 without
ASCII escaping. Floats must be finite. Callers decide which operational fields
(timestamps, run IDs) are excluded before hashing.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any


def _check_finite(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("canonical JSON forbids NaN/Infinity")
    if isinstance(value, dict):
        for item in value.values():
            _check_finite(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _check_finite(item)


def canonical_json(value: Any) -> str:
    _check_finite(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def content_sha256(value: Any) -> str:
    return sha256_text(canonical_json(value))


def stable_id(prefix: str, value: Any, length: int = 32) -> str:
    """Locally assigned ID: type prefix plus a stable content hash."""
    return f"{prefix}:{content_sha256(value)[:length]}"
