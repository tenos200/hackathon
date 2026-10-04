"""Model access for extraction and contextual checking (pipeline only).

`RecordedClient` replays stored responses (offline tests and resume). The
`OpenAIClient` makes a paid call only when an API key is present, the model
and its pricing are set and marked verified in non-secret config, and the
shared budget ledger approves a reservation. Completed responses are cached by
(canonical text hash, prompt/schema version, model, settings) and never
re-requested. A failure after sending is recorded as an uncertain outcome and
stays charged; it is never assumed to be a free retry.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from atlas.budget import BudgetExceeded, BudgetLedger
from atlas.hashing import canonical_json, content_sha256


@dataclass(frozen=True)
class ModelRequest:
    purpose: str             # extraction | checking
    model: str
    prompt_version: str
    schema_name: str
    schema: dict[str, Any]
    instructions: str
    input_text: str
    settings: dict[str, Any]
    max_output_tokens: int
    content_hash: str        # canonical text hash of the source or candidate being processed

    def cache_key(self) -> str:
        return content_sha256({
            "purpose": self.purpose, "model": self.model, "prompt_version": self.prompt_version,
            "schema": self.schema, "settings": self.settings, "max_output_tokens": self.max_output_tokens,
            "content_hash": self.content_hash, "input_sha256": content_sha256(self.input_text),
        })


@dataclass(frozen=True)
class ModelResponse:
    response_id: str
    model: str
    output: dict[str, Any]
    input_tokens: int | None
    output_tokens: int | None
    recorded: bool           # True when served from the cache/recording


class MissingRecording(LookupError):
    pass


class PaidCallRefused(RuntimeError):
    pass


class ModelClient(Protocol):
    def call(self, request: ModelRequest) -> ModelResponse: ...


class ResponseCache:
    """Private cache of completed responses: work/model/responses/<key>.json."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory / "responses"
        self.uncertain = directory / "uncertain.jsonl"
        self.directory.mkdir(parents=True, exist_ok=True)

    def get(self, key: str) -> ModelResponse | None:
        path = self.directory / f"{key}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return ModelResponse(response_id=data["response_id"], model=data["model"], output=data["output"],
                             input_tokens=data.get("input_tokens"), output_tokens=data.get("output_tokens"),
                             recorded=True)

    def put(self, key: str, response: ModelResponse, raw: dict[str, Any] | None) -> None:
        payload = {"response_id": response.response_id, "model": response.model, "output": response.output,
                   "input_tokens": response.input_tokens, "output_tokens": response.output_tokens, "raw": raw}
        (self.directory / f"{key}.json").write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    def record_uncertain(self, key: str, request: ModelRequest, reason: str) -> None:
        with self.uncertain.open("a", encoding="utf-8") as handle:
            handle.write(canonical_json({"key": key, "purpose": request.purpose, "model": request.model,
                                         "content_hash": request.content_hash, "reason": reason}) + "\n")


class RecordedClient:
    def __init__(self, cache: ResponseCache) -> None:
        self.cache = cache

    def call(self, request: ModelRequest) -> ModelResponse:
        hit = self.cache.get(request.cache_key())
        if hit is None:
            raise MissingRecording(f"no recorded {request.purpose} response for {request.content_hash[:12]} "
                                   f"(prompt {request.prompt_version}, model {request.model}); offline mode cannot call a model")
        return hit


def verified_pricing(models_config: dict[str, Any], model: str) -> tuple[float, float]:
    pricing = models_config.get("pricing", {}).get(model)
    if not pricing or pricing.get("input_usd_per_1m") is None or pricing.get("output_usd_per_1m") is None:
        raise PaidCallRefused(f"no pricing configured for model {model!r}")
    if not pricing.get("verified_by") or not pricing.get("verified_at"):
        raise PaidCallRefused(f"pricing for {model!r} has not been verified by a team member")
    return float(pricing["input_usd_per_1m"]), float(pricing["output_usd_per_1m"])


class OpenAIClient:
    """The one configurable OpenAI client. Uses the Responses API with a strict JSON schema."""

    def __init__(self, api_key: str | None, ledger: BudgetLedger, cache: ResponseCache,
                 models_config: dict[str, Any], sdk_client: Any | None = None) -> None:
        if not api_key and sdk_client is None:
            raise PaidCallRefused("OPENAI_API_KEY is not set; use recorded responses")
        self.ledger = ledger
        self.cache = cache
        self.models_config = models_config
        self._api_key = api_key
        self._sdk = sdk_client

    def _client(self):
        if self._sdk is None:
            from openai import OpenAI  # pipeline dependency only; never installed in the API service

            self._sdk = OpenAI(api_key=self._api_key, max_retries=0)  # retries are budgeted explicitly
        return self._sdk

    def call(self, request: ModelRequest) -> ModelResponse:
        key = request.cache_key()
        hit = self.cache.get(key)
        if hit is not None:
            return hit
        input_price, output_price = verified_pricing(self.models_config, request.model)
        estimated_input_tokens = len(request.instructions) // 3 + len(request.input_text) // 3 + 200
        estimate = (estimated_input_tokens * input_price + request.max_output_tokens * output_price) / 1_000_000
        reservation = self.ledger.reserve_usd("openai", estimate, f"{request.purpose}:{request.content_hash[:16]}")
        try:
            raw = self._client().responses.create(
                model=request.model, instructions=request.instructions, input=request.input_text,
                max_output_tokens=request.max_output_tokens, store=False,
                text={"format": {"type": "json_schema", "name": request.schema_name, "schema": request.schema,
                                 "strict": True}},
                **request.settings)
        except Exception as exc:  # the request may have reached the provider: stays charged
            self.ledger.settle(reservation, "uncertain")
            self.cache.record_uncertain(key, request, f"{type(exc).__name__}")
            raise
        usage = getattr(raw, "usage", None)
        in_tokens = getattr(usage, "input_tokens", None)
        out_tokens = getattr(usage, "output_tokens", None)
        actual = None
        if in_tokens is not None and out_tokens is not None:
            actual = (in_tokens * input_price + out_tokens * output_price) / 1_000_000
        self.ledger.settle(reservation, "completed", actual)
        try:
            output = json.loads(raw.output_text)
        except (json.JSONDecodeError, AttributeError) as exc:
            self.cache.record_uncertain(key, request, f"unparseable output: {type(exc).__name__}")
            raise
        response = ModelResponse(response_id=raw.id, model=getattr(raw, "model", request.model), output=output,
                                 input_tokens=in_tokens, output_tokens=out_tokens, recorded=False)
        dump = raw.model_dump(mode="json") if hasattr(raw, "model_dump") else None
        self.cache.put(key, response, dump)
        return response


def make_client(*, offline: bool, api_key: str | None, ledger: BudgetLedger | None, cache: ResponseCache,
                models_config: dict[str, Any]) -> ModelClient:
    if offline or not api_key:
        return RecordedClient(cache)
    if ledger is None:
        raise BudgetExceeded("paid model calls need the shared budget ledger")
    return OpenAIClient(api_key, ledger, cache, models_config)
