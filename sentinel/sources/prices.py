"""Daily adjusted close prices (Yahoo Finance chart endpoint) for derived statistics only.

Used by the event-validation harness to compute log returns, market-model
abnormal returns and CARs. Raw prices stay in the local disk cache under
CACHE_DIR; they are never written to the repository or to public/ — only
derived statistics (CAR, IC, quintile spreads) are published.
"""

from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta, timezone
from typing import List, Tuple

from sentinel.http import _cache_path, fetch_bytes

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
HEADERS = {"User-Agent": "Mozilla/5.0"}
SOURCE_NOTE = "Yahoo Finance chart API (adjusted close); used for derived statistics only, raw prices not redistributed"

_last_network = 0.0
MIN_INTERVAL_S = 0.35


def _epoch(d: str) -> int:
    return int(datetime.fromisoformat(d).replace(tzinfo=timezone.utc).timestamp())


def yahoo_symbol(ticker: str) -> str:
    return ticker.upper().replace(".", "-")


def parse_chart(payload: dict) -> List[Tuple[str, float]]:
    """(local trading date ISO, adjusted close) pairs from a v8 chart payload, ascending, None rows dropped."""
    res = ((payload or {}).get("chart") or {}).get("result") or []
    if not res:
        return []
    r = res[0]
    ts = r.get("timestamp") or []
    ind = r.get("indicators") or {}
    adj = ((ind.get("adjclose") or [{}])[0] or {}).get("adjclose")
    if adj is None:
        adj = ((ind.get("quote") or [{}])[0] or {}).get("close") or []
    offset = int((r.get("meta") or {}).get("gmtoffset") or 0)
    out = {}
    for t, v in zip(ts, adj):
        if v is None or v <= 0:
            continue
        d = datetime.fromtimestamp(t + offset, tz=timezone.utc).date().isoformat()
        out[d] = float(v)
    return sorted(out.items())


def _fresh_in_cache(url: str, params: dict, ttl: int) -> bool:
    path = _cache_path(url, params)
    meta = path.with_suffix(".meta")
    try:
        return path.exists() and time.time() - json.loads(meta.read_text())["ts"] < ttl
    except Exception:
        return False


def daily_adjclose(symbol: str, start: str, end: str) -> List[Tuple[str, float]]:
    """Daily adjusted close for [start, end] (ISO dates), disk-cached under CACHE_DIR."""
    global _last_network
    sym = yahoo_symbol(symbol)
    params = {"period1": _epoch(start), "period2": _epoch(end) + 86400, "interval": "1d", "events": "div,split"}
    recent = date.fromisoformat(end) >= date.today() - timedelta(days=7)
    ttl = 6 * 3600 if recent else 365 * 86400
    url = CHART_URL.format(symbol=sym)
    if not _fresh_in_cache(url, params, ttl):  # polite pacing for live requests only
        wait = MIN_INTERVAL_S - (time.time() - _last_network)
        if wait > 0:
            time.sleep(wait)
        _last_network = time.time()
    body, _ = fetch_bytes(url, params=params, ttl=ttl, headers=HEADERS)
    return [(d, v) for d, v in parse_chart(json.loads(body)) if start <= d <= end]
