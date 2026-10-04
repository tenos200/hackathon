"""Research assistant service (separate deployment from the read API).

Run:  uvicorn atlas.agent.main:app --host 0.0.0.0 --port $PORT

It loads the same published snapshot as the read API (read-only) and holds the
only model credential, `ATLAS_AGENT_OPENAI_API_KEY`, which the read API refuses
to start with. The read API itself never calls a model. The service is ready
only when the snapshot loads AND a key, model, per-token prices and a positive
daily budget are configured; otherwise every chat request is refused.
"""

from __future__ import annotations

import logging
import os
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from atlas.agent.service import AssistantFailed, BudgetRefused, DailyBudget, ModelClient, Turn, answer
from atlas.api import dto
from atlas.api.main import RateLimiter, _client_key, build_store
from atlas.api.settings import load_settings, parse_origins

log = logging.getLogger("atlas.agent")
AGENT_ENV = ("ATLAS_AGENT_OPENAI_API_KEY", "ATLAS_AGENT_MODEL", "ATLAS_AGENT_INPUT_USD_PER_1M",
             "ATLAS_AGENT_OUTPUT_USD_PER_1M", "ATLAS_AGENT_DAILY_BUDGET_USD", "ATLAS_AGENT_RATE_LIMIT_PER_MINUTE")
DISCLAIMER = dto.Warning(code="AI_GENERATED", message=(
    "Written by an AI assistant from the atlas records it looked up. Check the cited records; uncited or "
    "'unsupported' text is not backed by the atlas. Not medical advice."))


@dataclass(frozen=True)
class AgentConfig:
    model: str | None
    api_key: str | None
    budget: DailyBudget | None
    rate_per_minute: int
    errors: tuple[str, ...]


def load_agent_config(env: dict[str, str]) -> AgentConfig:
    errors = []
    api_key = env.get("ATLAS_AGENT_OPENAI_API_KEY") or None
    model = env.get("ATLAS_AGENT_MODEL") or None
    if not api_key:
        errors.append("ATLAS_AGENT_OPENAI_API_KEY is not set")
    if not model:
        errors.append("ATLAS_AGENT_MODEL is not set")
    numbers = {}
    for name in ("ATLAS_AGENT_INPUT_USD_PER_1M", "ATLAS_AGENT_OUTPUT_USD_PER_1M", "ATLAS_AGENT_DAILY_BUDGET_USD"):
        try:
            numbers[name] = float(env.get(name, ""))
            if numbers[name] <= 0:
                raise ValueError
        except ValueError:
            errors.append(f"{name} must be a positive number")
    try:
        rate = int(env.get("ATLAS_AGENT_RATE_LIMIT_PER_MINUTE", "10"))
    except ValueError:
        errors.append("ATLAS_AGENT_RATE_LIMIT_PER_MINUTE must be an integer")
        rate = 10
    budget = None if errors else DailyBudget(cap_usd=numbers["ATLAS_AGENT_DAILY_BUDGET_USD"],
                                             input_usd_per_1m=numbers["ATLAS_AGENT_INPUT_USD_PER_1M"],
                                             output_usd_per_1m=numbers["ATLAS_AGENT_OUTPUT_USD_PER_1M"])
    return AgentConfig(model=model, api_key=api_key, budget=budget, rate_per_minute=rate, errors=tuple(errors))


class OpenAIResponses:
    """Adapter over the OpenAI Responses API; omits unset arguments."""

    def __init__(self, api_key: str) -> None:
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key, timeout=60, max_retries=0)

    def create(self, **kwargs):
        return self._client.responses.create(**{k: v for k, v in kwargs.items() if v is not None})


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    messages: list[ChatMessage] = Field(min_length=1, max_length=12)
    focus_id: str | None = Field(default=None, min_length=1, max_length=180)


def _error(status: int, code: str, message: str, rid: str, retryable: bool, snapshot_id: str | None,
           headers: dict[str, str] | None = None) -> JSONResponse:
    body = dto.ErrorEnvelope(contract_version=dto.CONTRACT_VERSION, snapshot_id=snapshot_id,
                             error=dto.ErrorBody(code=code, message=message, request_id=rid, retryable=retryable))
    return JSONResponse(status_code=status, content=body.model_dump(mode="json"),
                        headers={"X-Request-ID": rid, **(headers or {})})


def create_agent_app(env: dict[str, str] | None = None, client: ModelClient | None = None, store=None) -> FastAPI:
    env = dict(os.environ if env is None else env)
    config = load_agent_config(env)
    store_settings = load_settings({k: v for k, v in env.items() if k not in AGENT_ENV})
    try:
        origins = parse_origins(env.get("ATLAS_ALLOWED_ORIGINS"))
    except ValueError:
        origins = ()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.store = store
        app.state.not_ready = list(config.errors)
        if store is None:
            try:
                app.state.store = build_store(store_settings)
            except Exception as exc:
                app.state.not_ready.append(f"snapshot: {type(exc).__name__}: {exc}")
        if not app.state.not_ready:
            app.state.client = client or OpenAIResponses(config.api_key)
            log.info("assistant ready: model=%s snapshot=%s", config.model, app.state.store.snapshot_id)
        else:
            log.error("ASSISTANT NOT READY: %s", "; ".join(app.state.not_ready))
        yield

    app = FastAPI(title="Rare Disease Atlas research assistant", lifespan=lifespan, openapi_url=None,
                  docs_url=None, redoc_url=None)
    app.add_middleware(CORSMiddleware, allow_origins=list(origins), allow_credentials=False,
                       allow_methods=["GET", "POST", "OPTIONS"], allow_headers=["Accept", "Content-Type"],
                       expose_headers=["X-Request-ID", "Retry-After"])
    limiter = RateLimiter(config.rate_per_minute)

    def snap(request: Request) -> str | None:
        s = getattr(request.app.state, "store", None)
        return s.snapshot_id if s is not None else None

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, exc: RequestValidationError):
        return _error(422, "INVALID_REQUEST", "Send {messages: [{role, content}], focus_id?}; 1-12 messages, "
                      "each 1-4000 characters, the last from the user.", uuid.uuid4().hex, False, snap(request))

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz(request: Request):
        ready = not request.app.state.not_ready
        body = {"status": "ready" if ready else "not_ready", "snapshot_id": snap(request),
                "model": config.model if ready else None,
                "budget": config.budget.status() if (ready and config.budget) else None}
        return JSONResponse(status_code=200 if ready else 503, content=body)

    @app.post("/v1/assistant/chat")
    def chat(request: Request, body: ChatRequest):
        rid = uuid.uuid4().hex
        if request.app.state.not_ready:
            return _error(503, "ASSISTANT_UNAVAILABLE", "The research assistant is not configured or the data is "
                          "not loaded.", rid, True, snap(request))
        wait = limiter.check(_client_key(request))
        if wait is not None:
            return _error(429, "RATE_LIMITED", "Too many questions; please wait.", rid, True, snap(request),
                          {"Retry-After": str(wait)})
        if body.messages[-1].role != "user":
            return _error(422, "INVALID_REQUEST", "The last message must be from the user.", rid, False, snap(request))
        store = request.app.state.store
        try:
            data = answer(request.app.state.client, config.model, config.budget, store,
                          [Turn(m.role, m.content) for m in body.messages], body.focus_id)
        except BudgetRefused as exc:
            return _error(503, "BUDGET_EXHAUSTED", str(exc), rid, False, snap(request))
        except AssistantFailed as exc:
            return _error(502, "ASSISTANT_FAILED", str(exc), rid, True, snap(request))
        except Exception as exc:  # model provider errors: no details leak to the browser
            log.error("assistant error rid=%s: %s: %s", rid, type(exc).__name__, exc)
            return _error(502, "ASSISTANT_FAILED", "The AI provider did not answer; please retry.", rid, True,
                          snap(request))
        log.info("assistant answered rid=%s tools=%d cost=%.5f", rid, len(data["tools_used"]),
                 data["usage"]["cost_usd"])
        return JSONResponse(content={"contract_version": dto.CONTRACT_VERSION, "snapshot_id": store.snapshot_id,
                                     "data_mode": store.data_mode, "data": data,
                                     "warnings": [DISCLAIMER.model_dump()]}, headers={"X-Request-ID": rid})

    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
app = create_agent_app()
