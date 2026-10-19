"""Shared HTTP client with a small TTL disk cache.

All outbound data access goes through `fetch_bytes` / `fetch_json` so that every
source gets the same User-Agent, retry policy and cache behaviour.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

import httpx

from sentinel.config import CACHE_DIR, SEC_USER_AGENT

_client: Optional[httpx.Client] = None


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(
            timeout=httpx.Timeout(20.0, connect=8.0),
            follow_redirects=True,
            headers={"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"},
        )
    return _client


def _cache_path(url: str, params: Optional[Dict[str, Any]]) -> Any:
    key = hashlib.sha256((url + "?" + json.dumps(params or {}, sort_keys=True)).encode()).hexdigest()[:40]
    return CACHE_DIR / f"{key}.bin"


def fetch_bytes(
    url: str,
    params: Optional[Dict[str, Any]] = None,
    ttl: int = 6 * 3600,
    headers: Optional[Dict[str, str]] = None,
    retries: int = 2,
) -> Tuple[bytes, str]:
    """Return (body, retrieved_at_iso). Serves from disk cache when fresh."""
    path = _cache_path(url, params)
    meta = path.with_suffix(".meta")
    if ttl > 0 and path.exists() and meta.exists():
        try:
            m = json.loads(meta.read_text())
            if time.time() - m["ts"] < ttl:
                return path.read_bytes(), m["retrieved_at"]
        except Exception:
            pass

    last_exc: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            r = client().get(url, params=params, headers=headers)
            if r.status_code == 429 or r.status_code >= 500:
                raise httpx.HTTPStatusError(f"HTTP {r.status_code}", request=r.request, response=r)
            r.raise_for_status()
            body = r.content
            retrieved = utcnow_iso()
            try:
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                path.write_bytes(body)
                meta.write_text(json.dumps({"ts": time.time(), "retrieved_at": retrieved, "url": url}))
            except OSError:
                pass
            return body, retrieved
        except Exception as exc:  # network errors, 5xx, 429
            last_exc = exc
            if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None and exc.response.status_code < 500 and exc.response.status_code != 429:
                break
            time.sleep(0.6 * (attempt + 1))

    # Stale cache is better than nothing.
    if path.exists() and meta.exists():
        m = json.loads(meta.read_text())
        return path.read_bytes(), m["retrieved_at"]
    raise RuntimeError(f"fetch failed: {url}: {last_exc}")


def fetch_json(url: str, params: Optional[Dict[str, Any]] = None, ttl: int = 6 * 3600, headers: Optional[Dict[str, str]] = None) -> Tuple[Any, str]:
    body, retrieved = fetch_bytes(url, params=params, ttl=ttl, headers=headers)
    return json.loads(body), retrieved
