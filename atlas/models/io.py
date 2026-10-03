"""Validated JSONL reading/writing. Every stage reads and writes through here."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, TypeVar

from pydantic import BaseModel, ValidationError

from atlas.hashing import canonical_json

M = TypeVar("M", bound=BaseModel)


class RecordValidationError(ValueError):
    pass


def read_jsonl(path: Path, model: type[M]) -> list[M]:
    if not path.exists():
        return []
    records: list[M] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(model.model_validate_json(line))
        except ValidationError as exc:
            raise RecordValidationError(f"{path}:{number}: {model.__name__} invalid: {exc}") from exc
    return records


def write_jsonl(path: Path, records: Iterable[BaseModel], sort_key: str | None = "id") -> None:
    rows = [r.model_dump(mode="json") for r in records]
    if sort_key:
        rows.sort(key=lambda r: str(r[sort_key]))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(canonical_json(r) + "\n" for r in rows), encoding="utf-8")
