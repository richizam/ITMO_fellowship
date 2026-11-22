"""Shared HTTP client with an on-disk cache and per-host pacing.

Every external lookup goes through here, so re-running a dossier (or the demo
examples) does not hit OpenAlex / ORCID / Google Scholar again.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from urllib.parse import urlencode, urlparse

import httpx

from app import config

BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0 Safari/537.36"
)
API_UA = "FellowshipCandidateIntelligence/0.1 (ITMO Fellowship; research-assessment tool)"

# minimum seconds between two live requests to the same host
MIN_INTERVAL = {
    "scholar.google.com": 3.0,
    "api.openalex.org": 0.15,
    "pub.orcid.org": 0.2,
    "api.elsevier.com": 0.3,
    "api.crossref.org": 0.2,
    "en.itmo.ru": 0.3,
    "www.ifmo.ru": 0.3,
}


class FetchError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class CachedHttp:
    def __init__(self, cache_dir: Path = config.CACHE_DIR, ttl_days: int = config.CACHE_TTL_DAYS):
        self.cache_dir = cache_dir
        self.ttl = ttl_days * 86400
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()
        self.client = httpx.Client(
            timeout=config.HTTP_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": API_UA},
        )
        self.live_requests = 0

    # -- cache -------------------------------------------------------------------
    def _path(self, url: str) -> Path:
        host = urlparse(url).netloc or "other"
        h = hashlib.sha1(url.encode()).hexdigest()
        return self.cache_dir / host / f"{h}.json"

    def _read(self, url: str) -> dict | None:
        p = self._path(url)
        if not p.exists():
            return None
        try:
            entry = json.loads(p.read_text())
        except json.JSONDecodeError:
            return None
        if time.time() - entry.get("t", 0) > self.ttl:
            return None
        return entry

    def _write(self, url: str, status: int, body: str) -> None:
        p = self._path(url)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"t": time.time(), "url": url, "status": status, "body": body}))

    def _pace(self, host: str) -> None:
        gap = MIN_INTERVAL.get(host, 0.1)
        with self._lock:
            wait = self._last.get(host, 0) + gap - time.time()
            if wait > 0:
                time.sleep(wait)
            self._last[host] = time.time()

    # -- public ------------------------------------------------------------------
    def get(self, url: str, params: dict | None = None, headers: dict | None = None,
            cache_key_params: dict | None = None, browser: bool = False) -> tuple[int, str]:
        """GET with cache. `cache_key_params` lets secrets (api keys) stay out of the cache key."""
        full = url + ("?" + urlencode(params) if params else "")
        key = url + ("?" + urlencode(cache_key_params if cache_key_params is not None else params or {}))
        hit = self._read(key)
        if hit is not None:
            return hit["status"], hit["body"]
        host = urlparse(url).netloc
        self._pace(host)
        h = dict(headers or {})
        if browser:
            h.setdefault("User-Agent", BROWSER_UA)
            h.setdefault("Accept-Language", "en-US,en;q=0.9")
        try:
            r = self.client.get(full, headers=h)
        except httpx.HTTPError as e:
            raise FetchError(f"{host}: {type(e).__name__}: {e}") from e
        self.live_requests += 1
        if r.status_code == 200 or r.status_code == 404:
            self._write(key, r.status_code, r.text)
        return r.status_code, r.text

    def get_json(self, url: str, params: dict | None = None, headers: dict | None = None,
                 cache_key_params: dict | None = None) -> dict | None:
        status, body = self.get(url, params, headers, cache_key_params)
        if status == 404:
            return None
        if status != 200:
            raise FetchError(f"{urlparse(url).netloc} returned HTTP {status}", status)
        try:
            return json.loads(body)
        except json.JSONDecodeError as e:
            raise FetchError(f"{urlparse(url).netloc}: invalid JSON") from e


_shared: CachedHttp | None = None


def http() -> CachedHttp:
    global _shared
    if _shared is None:
        _shared = CachedHttp()
    return _shared
