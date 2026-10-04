"""Caching source client used by every adapter (pipeline only, never the API).

Every capture records response bytes, URL, status, non-secret headers, fetch
time, backend, requested transformation and SHA-256. Only approved URLs (exact
web pages from config, or fixed structured-API prefixes whose parameters come
from config IDs) are fetched; fetched text never changes destinations. A
failed or capped retrieval raises `FetchError`, which callers record as
incomplete coverage, never as an empty source or a guessed record.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from atlas.hashing import canonical_json, sha256_bytes, sha256_text

BRIGHTDATA_ENDPOINT = "https://api.brightdata.com/request"
# Request parameters that are credentials or personal contact details: sent, never recorded.
PRIVATE_PARAMS = frozenset({"api_key", "email"})
KEPT_HEADERS = ("content-type", "last-modified", "etag", "date", "content-length", "x-ratelimit-remaining")
TRANSIENT_STATUSES = {429, 500, 502, 503, 504}
MAX_RETRIES = 2


class FetchError(RuntimeError):
    def __init__(self, url: str, reason: str) -> None:
        super().__init__(f"{url}: {reason}")
        self.url = url
        self.reason = reason


@dataclass(frozen=True)
class Capture:
    target_url: str
    request_url: str
    backend: str               # direct | brightdata | recorded_import
    transformation: str | None  # e.g. markdown for Bright Data data_format
    status: int
    headers: dict[str, str]
    fetched_at: str
    raw_sha256: str
    body_path: str

    def body(self, cache_dir: Path) -> bytes:
        return (cache_dir / self.body_path).read_bytes()


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass(frozen=True)
class ApprovedPage:
    url: str
    fetch_backend: str          # direct | brightdata
    transformation: str | None  # None/raw HTML, or "markdown" (Bright Data data_format)
    expect_text: str | None     # reviewer-supplied marker proving the real target page was received
    purpose: str


def load_approved_pages(path: Path) -> dict[str, ApprovedPage]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    pages: dict[str, ApprovedPage] = {}
    for row in data.get("pages", []):
        page = ApprovedPage(url=row["url"], fetch_backend=row["fetch_backend"],
                            transformation=row.get("transformation"), expect_text=row.get("expect_text"),
                            purpose=row["purpose"])
        if page.fetch_backend not in ("direct", "brightdata"):
            raise ValueError(f"{page.url}: fetch_backend must be direct or brightdata")
        if page.transformation not in (None, "markdown"):
            raise ValueError(f"{page.url}: transformation must be null or markdown")
        pages[page.url] = page
    return pages


def target_url(url: str, params: dict[str, str] | None = None) -> str:
    """The recorded target: the URL plus non-private parameters in sorted order."""
    public = {k: v for k, v in (params or {}).items() if k not in PRIVATE_PARAMS}
    return url if not public else f"{url}?{'&'.join(f'{k}={v}' for k, v in sorted(public.items()))}"


class Fetcher:
    def __init__(self, cache_dir: Path, *, offline: bool, api_prefixes: tuple[str, ...] = (),
                 approved_pages: dict[str, ApprovedPage] | None = None,
                 client_factory: Callable[[], object] | None = None,
                 min_interval_seconds: dict[str, float] | None = None,
                 brightdata_key: str | None = None, brightdata_zone: str | None = None,
                 brightdata_ledger: "object | None" = None,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], str] = utc_now) -> None:
        self.cache_dir = cache_dir
        self.offline = offline
        self.api_prefixes = api_prefixes
        self.approved_pages = approved_pages or {}
        self._client_factory = client_factory
        self._client = None
        self.min_interval = min_interval_seconds or {}
        self._last_request: dict[str, float] = {}
        self._bd_key = brightdata_key
        self._bd_zone = brightdata_zone
        self._bd_ledger = brightdata_ledger
        self.sleep = sleep
        self.clock = clock
        (cache_dir / "raw").mkdir(parents=True, exist_ok=True)
        self.index_path = cache_dir / "index.jsonl"

    # ------------------------------------------------------------ cache

    @staticmethod
    def cache_key(target_url: str, backend: str, transformation: str | None) -> str:
        return sha256_text(canonical_json([target_url, backend, transformation]))

    def _index(self) -> dict[str, Capture]:
        captures: dict[str, Capture] = {}
        if self.index_path.exists():
            for line in self.index_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    key = row.pop("key")
                    captures[key] = Capture(**row)
        return captures

    def cached(self, target_url: str, backend: str = "direct", transformation: str | None = None) -> Capture | None:
        return self._index().get(self.cache_key(target_url, backend, transformation))

    def _store(self, key: str, capture: Capture, body: bytes) -> Capture:
        path = self.cache_dir / capture.body_path
        if not path.exists():
            path.write_bytes(body)
        with self.index_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"key": key, **asdict(capture)}, sort_keys=True) + "\n")
        return capture

    def import_recorded_capture(self, target_url: str, backend: str, transformation: str | None,
                                fetched_at: str, status: int, body: bytes, headers: dict[str, str] | None = None) -> Capture:
        """Import an existing team capture that records target URL, backend, transformation and capture time."""
        if backend not in ("direct", "brightdata"):
            raise FetchError(target_url, "recorded capture must name its original backend")
        if status != 200 or not body:
            raise FetchError(target_url, f"recorded capture is not a successful target response (status {status})")
        digest = sha256_bytes(body)
        capture = Capture(target_url=target_url, request_url=target_url, backend=backend,
                          transformation=transformation, status=status, headers=headers or {},
                          fetched_at=fetched_at, raw_sha256=digest, body_path=f"raw/{digest}")
        return self._store(self.cache_key(target_url, backend, transformation), capture, body)

    # ------------------------------------------------------------ network

    def _approved(self, url: str) -> ApprovedPage | None:
        if url in self.approved_pages:
            return self.approved_pages[url]
        if any(url.startswith(prefix) for prefix in self.api_prefixes):
            return None
        raise FetchError(url, "URL is not on the approved list for this run")

    def _http(self):
        if self._client is None:
            if self._client_factory is None:
                import httpx  # pipeline dependency only

                self._client_factory = lambda: httpx.Client(timeout=30.0, follow_redirects=True,
                                                            headers={"User-Agent": "atlas-pipeline/0.1"})
            self._client = self._client_factory()
        return self._client

    def _throttle(self, url: str) -> None:
        host = urlsplit(url).netloc
        interval = self.min_interval.get(host, 0.0)
        last = self._last_request.get(host)
        if last is not None and interval > 0:
            wait = interval - (time.monotonic() - last)
            if wait > 0:
                self.sleep(wait)
        self._last_request[host] = time.monotonic()

    def _request(self, method: str, url: str, **kwargs):
        last_error = "no attempt"
        for attempt in range(MAX_RETRIES + 1):
            self._throttle(url)
            try:
                response = self._http().request(method, url, **kwargs)
            except Exception as exc:  # network failure: transient
                last_error = f"network error: {type(exc).__name__}"
            else:
                if response.status_code not in TRANSIENT_STATUSES:
                    return response
                last_error = f"HTTP {response.status_code}"
            if attempt < MAX_RETRIES:
                self.sleep(min(2.0 ** attempt, 4.0))  # bounded backoff: 1s, 2s
        raise FetchError(url, f"failed after {MAX_RETRIES} retries ({last_error})")

    def get(self, url: str, *, params: dict[str, str] | None = None, backend: str | None = None,
            transformation: str | None = None) -> Capture:
        page = self._approved(url)
        if page is not None:
            backend = backend or page.fetch_backend
            transformation = transformation if transformation is not None else page.transformation
        backend = backend or "direct"
        target = target_url(url, params)
        key = self.cache_key(target, backend, transformation)
        hit = self._index().get(key)
        if hit is not None:
            return hit
        if self.offline:
            raise FetchError(target, "offline mode: no cached capture (network forbidden)")
        if backend == "brightdata":
            capture, body = self._fetch_brightdata(url, transformation)
        else:
            capture, body = self._fetch_direct(url, params, target)
        if page is not None and page.expect_text:
            decoded = body.decode("utf-8", errors="replace")
            if page.expect_text not in decoded:
                raise FetchError(url, "target page did not contain the reviewer's expected marker; "
                                      "not accepted as a document (possible error, block or consent page)")
        return self._store(key, capture, body)

    def _fetch_direct(self, url: str, params: dict[str, str] | None, target: str) -> tuple[Capture, bytes]:
        response = self._request("GET", url, params=params)
        body = response.content
        if response.status_code != 200 or not body:
            raise FetchError(url, f"target returned HTTP {response.status_code}; not accepted as a document")
        headers = {k: v for k, v in response.headers.items() if k.lower() in KEPT_HEADERS}
        digest = sha256_bytes(body)
        return Capture(target_url=target, request_url=target, backend="direct",
                       transformation=None, status=response.status_code, headers=headers,
                       fetched_at=self.clock(), raw_sha256=digest, body_path=f"raw/{digest}"), body

    def _fetch_brightdata(self, url: str, transformation: str | None) -> tuple[Capture, bytes]:
        if not self._bd_key or not self._bd_zone:
            raise FetchError(url, "Bright Data backend selected but BRIGHTDATA_API_KEY/BRIGHTDATA_UNLOCKER_ZONE are not set")
        if self._bd_ledger is None:
            raise FetchError(url, "Bright Data requires a configured provider budget")
        reservation = self._bd_ledger.reserve_request("brightdata", purpose=url)  # raises when the cap is reached
        payload = {"zone": self._bd_zone, "url": url, "format": "raw"}
        if transformation == "markdown":
            payload["data_format"] = "markdown"  # a transformation, not a value for `format`
        try:
            response = self._request("POST", BRIGHTDATA_ENDPOINT, json=payload,
                                     headers={"Authorization": f"Bearer {self._bd_key}"})
        except FetchError:
            self._bd_ledger.settle(reservation, outcome="uncertain")
            raise
        self._bd_ledger.settle(reservation, outcome="completed")
        body = response.content
        if response.status_code != 200 or not body:
            raise FetchError(url, f"Bright Data/target returned HTTP {response.status_code}; not accepted as a document")
        headers = {k: v for k, v in response.headers.items() if k.lower() in KEPT_HEADERS}
        digest = sha256_bytes(body)
        return Capture(target_url=url, request_url=BRIGHTDATA_ENDPOINT, backend="brightdata",
                       transformation=transformation, status=response.status_code, headers=headers,
                       fetched_at=self.clock(), raw_sha256=digest, body_path=f"raw/{digest}"), body
