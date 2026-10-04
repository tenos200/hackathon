"""One durable spending ledger shared by every paid pipeline stage.

Extraction, linking, checking, retries and optional Bright Data captures all
reserve against the same file, so separate commands cannot each spend the full
cap. A reservation counts as spent until it is settled; an uncertain outcome
(request may have reached the provider) stays charged at the reserved amount.
Repeat attempts are never assumed to be free. Paid calls require a positive
team-configured cap; a cap of zero (the committed default) forbids them.
"""

from __future__ import annotations

import fcntl
import json
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


class BudgetExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class Reservation:
    id: str
    provider: str
    amount_usd: float
    requests: int


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class BudgetLedger:
    def __init__(self, path: Path, config: dict, run_id: str | None = None,
                 command_cap_usd: float | None = None) -> None:
        self.path = path
        self.config = config
        self.run_id = run_id or uuid.uuid4().hex
        self.command_cap_usd = command_cap_usd
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)

    @classmethod
    def from_files(cls, ledger_path: Path, config_path: Path, command_cap_usd: float | None = None) -> "BudgetLedger":
        config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
        return cls(ledger_path, config, command_cap_usd=command_cap_usd)

    @contextmanager
    def _locked(self):
        with self.path.open("r+", encoding="utf-8") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield handle
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    @staticmethod
    def _events(handle) -> list[dict]:
        handle.seek(0)
        return [json.loads(line) for line in handle.read().splitlines() if line.strip()]

    @staticmethod
    def totals(events: list[dict], provider: str | None = None, run_id: str | None = None) -> tuple[float, int]:
        """Spent USD and request count, counting unsettled and uncertain reservations as spent."""
        reserves = {e["id"]: e for e in events if e["event"] == "reserve"}
        settles = {e["id"]: e for e in events if e["event"] == "settle"}
        usd, requests = 0.0, 0
        for rid, res in reserves.items():
            if provider is not None and res["provider"] != provider:
                continue
            if run_id is not None and res["run_id"] != run_id:
                continue
            settle = settles.get(rid)
            if settle is None or settle["outcome"] == "uncertain":
                usd += res["amount_usd"]
            elif settle["outcome"] == "completed":
                usd += settle["actual_usd"] if settle.get("actual_usd") is not None else res["amount_usd"]
            requests += 0 if settle is not None and settle["outcome"] == "not_sent" else 1
        return usd, requests

    def _append(self, handle, event: dict) -> None:
        handle.seek(0, 2)
        handle.write(json.dumps(event, sort_keys=True) + "\n")
        handle.flush()

    def reserve_usd(self, provider: str, estimated_usd: float, purpose: str) -> Reservation:
        shared_cap = float(self.config.get("shared_cap_usd", 0))
        provider_cap = float(self.config.get("providers", {}).get(provider, {}).get("cap_usd", 0))
        if estimated_usd <= 0:
            raise BudgetExceeded("a paid call needs a positive cost estimate from verified pricing config")
        if shared_cap <= 0 or provider_cap <= 0:
            raise BudgetExceeded(f"no positive configured budget for {provider}; paid calls are disabled")
        if self.command_cap_usd is None or self.command_cap_usd <= 0:
            raise BudgetExceeded("the command needs a positive --budget-usd cap")
        with self._locked() as handle:
            events = self._events(handle)
            total, _ = self.totals(events)
            provider_total, _ = self.totals(events, provider)
            run_total, _ = self.totals(events, run_id=self.run_id)
            if total + estimated_usd > shared_cap:
                raise BudgetExceeded(f"shared cap reached: {total:.4f} spent/reserved of {shared_cap:.4f} USD")
            if provider_total + estimated_usd > provider_cap:
                raise BudgetExceeded(f"{provider} cap reached: {provider_total:.4f} of {provider_cap:.4f} USD")
            if run_total + estimated_usd > self.command_cap_usd:
                raise BudgetExceeded(f"command cap reached: {run_total:.4f} of {self.command_cap_usd:.4f} USD")
            reservation = Reservation(uuid.uuid4().hex, provider, estimated_usd, 1)
            self._append(handle, {"event": "reserve", "id": reservation.id, "provider": provider,
                                  "amount_usd": estimated_usd, "run_id": self.run_id, "purpose": purpose, "at": _now()})
            return reservation

    def reserve_request(self, provider: str, purpose: str) -> Reservation:
        """Request-counted provider (Bright Data): `max_requests` cap plus optional per-request USD."""
        cfg = self.config.get("providers", {}).get(provider, {})
        max_requests = int(cfg.get("max_requests", 0))
        per_request = float(cfg.get("usd_per_request", 0) or 0)
        if max_requests <= 0:
            raise BudgetExceeded(f"no positive configured request budget for {provider}")
        with self._locked() as handle:
            events = self._events(handle)
            _, used = self.totals(events, provider)
            if used + 1 > max_requests:
                raise BudgetExceeded(f"{provider} request cap reached: {used} of {max_requests}")
            if per_request:
                total, _ = self.totals(events)
                if total + per_request > float(self.config.get("shared_cap_usd", 0)):
                    raise BudgetExceeded("shared cap reached")
            reservation = Reservation(uuid.uuid4().hex, provider, per_request, 1)
            self._append(handle, {"event": "reserve", "id": reservation.id, "provider": provider,
                                  "amount_usd": per_request, "run_id": self.run_id, "purpose": purpose, "at": _now()})
            return reservation

    def settle(self, reservation: Reservation, outcome: str, actual_usd: float | None = None) -> None:
        if outcome not in ("completed", "uncertain", "not_sent"):
            raise ValueError("outcome must be completed, uncertain or not_sent")
        with self._locked() as handle:
            self._append(handle, {"event": "settle", "id": reservation.id, "outcome": outcome,
                                  "actual_usd": actual_usd, "at": _now()})

    def summary(self) -> dict:
        with self._locked() as handle:
            events = self._events(handle)
        providers = sorted({e["provider"] for e in events if e["event"] == "reserve"})
        out = {"total_usd": round(self.totals(events)[0], 6), "providers": {}}
        for p in providers:
            usd, n = self.totals(events, p)
            out["providers"][p] = {"usd": round(usd, 6), "requests": n}
        return out
