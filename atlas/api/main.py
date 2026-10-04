"""FastAPI entry point: `uvicorn atlas.api.main:app`.

Startup only loads configuration and validates one pinned snapshot (or the
explicit synthetic fixture set). It never fetches sources, calls a model,
publishes, or chooses a "latest" snapshot. Product routes return 503 until the
pinned snapshot has been fully validated.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager

from fastapi import FastAPI, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from atlas.api import dto
from atlas.api.fixture_store import CONTRACTS_DIR, FixtureStore
from atlas.api.settings import Settings, load_settings
from atlas.api.store import NotFound, SnapshotStore

log = logging.getLogger("atlas.api")

ID_PARAM = Path(min_length=1, max_length=180)
EXPOSED_HEADERS = ["X-Request-ID", "Retry-After"]
OPERATIONAL_PATHS = {"/healthz", "/readyz", "/openapi.json", "/openapi-extension.json"}


class RateLimiter:
    """Bounded in-process sliding window per client address.

    A single-instance availability safeguard only; it is not distributed
    protection and the client address comes from the platform proxy header.
    """

    def __init__(self, per_minute: int, max_clients: int = 10_000) -> None:
        self.per_minute = per_minute
        self.max_clients = max_clients
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, client: str, now: float | None = None) -> int | None:
        """Return None if allowed, else seconds to wait."""
        if self.per_minute <= 0:
            return None
        now = time.monotonic() if now is None else now
        with self._lock:
            if client not in self._hits and len(self._hits) >= self.max_clients:
                self._hits.clear()  # bounded memory; resets rather than growing without limit
            window = self._hits.setdefault(client, deque())
            while window and now - window[0] >= 60:
                window.popleft()
            if len(window) >= self.per_minute:
                return max(1, int(60 - (now - window[0])) + 1)
            window.append(now)
            return None


def _client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _error(status: int, code: str, message: str, request_id: str, retryable: bool,
           snapshot_id: str | None, headers: dict[str, str] | None = None) -> JSONResponse:
    body = dto.ErrorEnvelope(
        contract_version=dto.CONTRACT_VERSION, snapshot_id=snapshot_id,
        error=dto.ErrorBody(code=code, message=message, request_id=request_id, retryable=retryable),
    )
    return JSONResponse(status_code=status, content=body.model_dump(mode="json"),
                        headers={"X-Request-ID": request_id, **(headers or {})})


def build_store(settings: Settings) -> SnapshotStore:
    if settings.config_errors:
        raise RuntimeError("; ".join(settings.config_errors))
    if settings.data_mode == "synthetic_fixture":
        return FixtureStore(settings.fixture_scenarios)
    if settings.data_mode == "real":
        from atlas.api.real_store import load_real_store  # read-only snapshot projection

        return load_real_store(settings)
    raise RuntimeError("No data mode configured")


def create_app(settings: Settings | None = None, store: SnapshotStore | None = None) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.store = store
        app.state.not_ready_reason = None
        if store is None:
            try:
                app.state.store = build_store(settings)
                log.info("ready: mode=%s snapshot=%s", settings.data_mode, app.state.store.snapshot_id)
            except Exception as exc:  # stay unready; never fall back to another snapshot or fixtures
                app.state.store = None
                app.state.not_ready_reason = f"{type(exc).__name__}: {exc}"
                log.error("NOT READY: %s", app.state.not_ready_reason)
        yield

    app = FastAPI(
        title="Rare Disease Atlas public read API", version=dto.CONTRACT_VERSION,
        lifespan=lifespan, openapi_url=None, docs_url=None, redoc_url=None,
    )
    app.state.settings = settings
    app.state.store = store
    app.state.not_ready_reason = None
    limiter = RateLimiter(settings.rate_limit_per_minute)
    contract_bytes = (CONTRACTS_DIR / "openapi.json").read_bytes()

    def current_store(request: Request) -> SnapshotStore | None:
        return request.app.state.store

    def snap(request: Request) -> str | None:
        s = current_store(request)
        return s.snapshot_id if s is not None else None

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        started = time.perf_counter()
        if request.url.path not in OPERATIONAL_PATHS and request.method != "OPTIONS":
            wait = limiter.check(_client_key(request))
            if wait is not None:
                return _error(429, "RATE_LIMITED", "Too many requests. Try again shortly.", request_id, True,
                              snap(request), {"Retry-After": str(wait)})
        try:
            response = await call_next(request)
        except Exception:  # unexpected failure: expose only the request ID
            log.exception("unhandled error request_id=%s path=%s", request_id, request.url.path)
            response = _error(500, "INTERNAL_ERROR", "The service could not complete the request.",
                              request_id, True, snap(request))
        response.headers["X-Request-ID"] = request_id
        log.info("%s %s %s %.1fms rid=%s", request.method, request.url.path, response.status_code,
                 (time.perf_counter() - started) * 1000, request_id)
        return response

    # Added after the request middleware so CORS headers also wrap error responses.
    app.add_middleware(
        CORSMiddleware, allow_origins=list(settings.allowed_origins), allow_credentials=False,
        allow_methods=["GET", "OPTIONS"], allow_headers=["Accept", "Content-Type"],
        expose_headers=EXPOSED_HEADERS, max_age=600,
    )

    @app.exception_handler(RequestValidationError)
    async def on_validation(request: Request, exc: RequestValidationError):
        fields = sorted({".".join(str(p) for p in err.get("loc", ())[1:]) or "request" for err in exc.errors()})
        return _error(422, "INVALID_REQUEST", f"Invalid request parameter(s): {', '.join(fields)}.",
                      request.state.request_id, False, snap(request))

    @app.exception_handler(StarletteHTTPException)
    async def on_http(request: Request, exc: StarletteHTTPException):
        if exc.status_code == 404:
            return _error(404, "NOT_FOUND", "The requested resource is not in this snapshot.",
                          request.state.request_id, False, snap(request))
        if exc.status_code == 405:
            return _error(405, "METHOD_NOT_ALLOWED", "This read-only API accepts GET requests only.",
                          request.state.request_id, False, snap(request))
        return _error(exc.status_code, "HTTP_ERROR", "The request could not be completed.",
                      request.state.request_id, exc.status_code >= 500, snap(request))

    def product(request: Request, fn) -> Response:
        s = current_store(request)
        if s is None:
            return _error(503, "SNAPSHOT_UNAVAILABLE", "The reviewed snapshot is not ready.",
                          request.state.request_id, True, None)
        try:
            result: dto.Dto = fn(s)
        except NotFound:
            return _error(404, "NOT_FOUND", "The requested resource is not in this snapshot.",
                          request.state.request_id, False, s.snapshot_id)
        return JSONResponse(content=result.model_dump(mode="json"))

    @app.get("/v1/meta")
    def get_meta(request: Request):
        return product(request, lambda s: s.meta())

    @app.get("/v1/search")
    def get_search(request: Request, q: str | None = None):
        trimmed = q.strip() if q is not None else ""
        if q is None or len(q) > 200 or not (1 <= len(trimmed) <= 200):
            return _error(422, "INVALID_REQUEST", "The search query must contain 1 to 200 characters.",
                          request.state.request_id, False, snap(request))
        return product(request, lambda s: s.search(trimmed))

    @app.get("/v1/contexts/{id}")
    def get_context(request: Request, id: str = ID_PARAM):
        return product(request, lambda s: s.context(id))

    @app.get("/v1/contexts/{id}/connections")
    def get_connections(request: Request, id: str = ID_PARAM):
        return product(request, lambda s: s.connections(id))

    @app.get("/v1/contexts/{id}/actions")
    def get_actions(request: Request, id: str = ID_PARAM):
        return product(request, lambda s: s.actions(id))

    @app.get("/v1/assertions/{id}")
    def get_assertion(request: Request, id: str = ID_PARAM):
        return product(request, lambda s: s.assertion(id))

    @app.get("/v1/calculations/{id}")
    def get_calculation(request: Request, id: str = ID_PARAM):
        return product(request, lambda s: s.calculation(id))

    # ---- proposed contract 1.1.0 additions (additive; see contracts/extension-1.1.0/)

    def extension(request: Request, fn) -> Response:
        def call(store):
            ext = getattr(store, "ext", None)
            if ext is None:
                raise NotFound("extension routes need a store that provides them")
            return fn(ext)
        return product(request, call)

    @app.get("/v1/entities/{id}")
    def get_entity(request: Request, id: str = ID_PARAM):
        return extension(request, lambda e: e.entity(id))

    @app.get("/v1/graph")
    def get_graph(request: Request, focus: str | None = None, depth: int = 1):
        if focus is None or not (1 <= len(focus) <= 180) or depth not in (1, 2):
            return _error(422, "INVALID_REQUEST", "graph needs focus (1-180 characters) and depth 1 or 2.",
                          request.state.request_id, False, snap(request))
        return extension(request, lambda e: e.graph(focus, depth))

    @app.get("/v1/paths")
    def get_paths(request: Request, to: str | None = None, max_length: int = 4,
                  from_: str | None = Query(None, alias="from")):
        if not from_ or not to or len(from_) > 180 or len(to) > 180 or not (1 <= max_length <= 5):
            return _error(422, "INVALID_REQUEST", "paths needs from and to (1-180 characters) and max_length 1-5.",
                          request.state.request_id, False, snap(request))
        return extension(request, lambda e: e.paths(from_, to, max_length))

    @app.get("/v1/atlas-map")
    def get_atlas_map(request: Request):
        return extension(request, lambda e: e.atlas_map())

    @app.get("/v1/clusters")
    def get_clusters(request: Request):
        return extension(request, lambda e: e.clusters())

    @app.get("/v1/contexts/{id}/network")
    def get_network(request: Request, id: str = ID_PARAM):
        return extension(request, lambda e: e.network(id))

    @app.get("/openapi-extension.json")
    def openapi_extension() -> Response:
        return Response(content=(CONTRACTS_DIR / "extension-1.1.0" / "openapi-extension.json").read_bytes(),
                        media_type="application/json")

    def health(request: Request, ready_status: str) -> JSONResponse:
        s = current_store(request)
        body = dto.Health(
            status=ready_status if s is not None else "not_ready",
            contract_version=dto.CONTRACT_VERSION,
            snapshot_id=s.snapshot_id if s is not None else None,
            data_mode=s.data_mode if s is not None else None,  # type: ignore[arg-type]
        )
        return JSONResponse(status_code=200 if s is not None else 503, content=body.model_dump(mode="json"))

    @app.get("/healthz")
    def healthz(request: Request):
        # Liveness: the process answers. It reports `ok` even while unready so the
        # platform does not restart a process that is correctly refusing to serve.
        s = current_store(request)
        body = dto.Health(status="ok", contract_version=dto.CONTRACT_VERSION,
                          snapshot_id=s.snapshot_id if s is not None else None,
                          data_mode=s.data_mode if s is not None else None)  # type: ignore[arg-type]
        return JSONResponse(content=body.model_dump(mode="json"))

    @app.get("/readyz")
    def readyz(request: Request):
        return health(request, "ready")

    @app.get("/openapi.json")
    def openapi() -> Response:
        # The committed shared contract is the documentation of record.
        return Response(content=contract_bytes, media_type="application/json")

    return app


def _configure_logging() -> None:
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


_configure_logging()
app = create_app()
