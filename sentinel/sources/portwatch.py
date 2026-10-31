"""IMF PortWatch live chokepoint data source.

Fetches coordinates/metadata and daily transits from the IMF PortWatch ArcGIS REST services.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from sentinel.http import _cache_path, fetch_json
from sentinel.models import Chokepoint, ChokepointPoint, Provenance

CHOKEPOINT_META_URL = (
    "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/"
    "PortWatch_chokepoints_database/FeatureServer/0/query"
)

CHOKEPOINT_DAILY_URL = (
    "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/"
    "Daily_Chokepoints_Data/FeatureServer/0/query"
)

CHOKEPOINT_ALIASES: Dict[str, str] = {
    "taiwan": "chokepoint11",
    "malacca": "chokepoint5",
    "hormuz": "chokepoint6",
    "suez": "chokepoint1",
    "panama": "chokepoint2",
    "bab-el-mandeb": "chokepoint4",
    "luzon": "chokepoint14",
    "korea": "chokepoint12",
}

PORTWATCH_URL = "https://portwatch.imf.org/pages/chokepoints"
PORTWATCH_LICENSE = "IMF PortWatch — free to use with attribution (see portwatch.imf.org terms)"


LAST_GOOD_SNAPSHOT = Path(__file__).resolve().parents[2] / "public" / "snapshots" / "chokepoints.json"
PAGE_SIZE = 1000  # the service's maxRecordCount


def _query(url: str, params: Dict[str, object], ttl: int, attempts: int = 5) -> Tuple[Dict, str]:
    """ArcGIS answers some valid queries with an HTTP-200 error body; never cache those, retry with backoff."""
    for attempt in range(attempts):
        data, retrieved_at = fetch_json(url, params=params, ttl=ttl if attempt == 0 else 0)
        if "error" not in data:
            return data, retrieved_at
        for path in (_cache_path(url, params), _cache_path(url, params).with_suffix(".meta")):
            path.unlink(missing_ok=True)
        time.sleep(min(2 ** attempt, 16))
    raise RuntimeError(f"PortWatch query failed after {attempts} attempts: {data.get('error')}")


def _fetch_metadata() -> Tuple[Dict[str, Dict[str, float | str]], str]:
    """Fetch 28 chokepoint coordinates and names (ttl 7 days)."""
    params = {
        "where": "1=1",
        "outFields": "portid,portname,lat,lon",
        "returnGeometry": "false",
        "f": "json",
    }
    data, retrieved_at = _query(CHOKEPOINT_META_URL, params, ttl=7 * 86400)
    meta: Dict[str, Dict[str, float | str]] = {}
    for feature in data.get("features", []):
        attr = feature.get("attributes", {})
        pid = attr.get("portid")
        if pid:
            meta[pid] = {
                "name": attr.get("portname", pid),
                "lat": float(attr.get("lat", 0.0)),
                "lon": float(attr.get("lon", 0.0)),
            }
    return meta, retrieved_at


def _fetch_daily_transits() -> Tuple[Dict[str, List[Dict[str, object]]], str]:
    """Fetch daily transits for the last 500 days paginated (ttl 6 hours)."""
    start_date = (datetime.now(timezone.utc) - timedelta(days=500)).strftime("%Y-%m-%d")
    offset = 0
    records_by_port: Dict[str, List[Dict[str, object]]] = {}
    last_retrieved = ""

    while True:
        params = {
            "where": f"date >= DATE '{start_date}'",
            "outFields": "date,portid,portname,n_total,capacity",
            "orderByFields": "date ASC",
            "resultOffset": offset,
            "resultRecordCount": PAGE_SIZE,
            "returnGeometry": "false",
            "f": "json",
        }
        data, retrieved_at = _query(CHOKEPOINT_DAILY_URL, params, ttl=6 * 3600)
        last_retrieved = retrieved_at
        features = data.get("features", [])
        if not features:
            break

        for feature in features:
            attr = feature.get("attributes", {})
            pid = attr.get("portid")
            if pid:
                records_by_port.setdefault(pid, []).append(attr)

        offset += len(features)
        if not data.get("exceededTransferLimit"):
            break

    # Sort each port's records ascending by date
    for pid in records_by_port:
        records_by_port[pid].sort(key=lambda x: str(x.get("date", "")))

    return records_by_port, last_retrieved


def _compute_chokepoints() -> Tuple[str, List[Chokepoint]]:
    """Return (as_of_date, items) for all 28 IMF PortWatch chokepoints.

    Calculates:
      - latest: last daily point
      - mean_7d: mean n_total over last 7 available days
      - baseline_365d: mean n_total over the 365 days before those 7 days
      - delta_pct: (mean_7d / baseline_365d - 1) * 100
      - zscore: (mean_7d - baseline_365d) / std(baseline) (None if std == 0)
      - series: last 120 days of ChokepointPoint records
    """
    meta, _ = _fetch_metadata()
    daily_records, retrieved_at = _fetch_daily_transits()

    chokepoints: List[Chokepoint] = []
    latest_dates: List[str] = []

    for portid, m in meta.items():
        pts_raw = daily_records.get(portid, [])
        if not pts_raw:
            continue

        pts = [
            ChokepointPoint(
                date=str(p.get("date")),
                n_total=int(p.get("n_total") or 0),
                capacity=float(p["capacity"]) if p.get("capacity") is not None else None,
            )
            for p in pts_raw
        ]

        latest_pt = pts[-1]
        latest_dates.append(latest_pt.date)

        # 7-day window
        window_7d = pts[-7:]
        n_7d = [p.n_total for p in window_7d]
        mean_7d = float(np.mean(n_7d)) if n_7d else None

        # 365-day baseline window immediately preceding the last 7 days
        prior_pts = pts[:-7]
        baseline_pts = prior_pts[-365:] if len(prior_pts) >= 365 else prior_pts
        n_baseline = [p.n_total for p in baseline_pts]

        if n_baseline:
            baseline_365d = float(np.mean(n_baseline))
            b_std = float(np.std(n_baseline))
            delta_pct = (
                ((mean_7d / baseline_365d) - 1.0) * 100.0
                if baseline_365d and mean_7d is not None
                else None
            )
            zscore = (
                (mean_7d - baseline_365d) / b_std
                if (b_std > 0 and mean_7d is not None)
                else None
            )
        else:
            baseline_365d = None
            delta_pct = None
            zscore = None

        series_120d = pts[-120:] if len(pts) >= 120 else pts

        provenance = Provenance(
            source="imf_portwatch",
            title=f"IMF PortWatch Daily Chokepoint Transits — {m['name']}",
            url=PORTWATCH_URL,
            retrieved_at=retrieved_at,
            license=PORTWATCH_LICENSE,
            locator=portid,
        )

        chokepoint = Chokepoint(
            id=portid,
            name=str(m["name"]),
            lat=float(m["lat"]),
            lon=float(m["lon"]),
            latest=latest_pt,
            mean_7d=round(mean_7d, 2) if mean_7d is not None else None,
            baseline_365d=round(baseline_365d, 2) if baseline_365d is not None else None,
            delta_pct=round(delta_pct, 2) if delta_pct is not None else None,
            zscore=round(zscore, 2) if zscore is not None else None,
            series=series_120d,
            provenance=provenance,
        )
        chokepoints.append(chokepoint)

    # Sort by portid number for consistent ordering
    chokepoints.sort(key=lambda x: int(x.id.replace("chokepoint", "")))
    as_of_date = max(latest_dates) if latest_dates else datetime.now(timezone.utc).strftime("%Y-%m-%d")

    return as_of_date, chokepoints


def get_chokepoints() -> Tuple[str, List[Chokepoint]]:
    """Live PortWatch figures; if the service fails or returns no transits, the last good published snapshot."""
    try:
        as_of, items = _compute_chokepoints()
        if items:
            return as_of, items
        err: Exception = RuntimeError("PortWatch returned no daily transits")
    except Exception as exc:
        err = exc
    if LAST_GOOD_SNAPSHOT.exists():
        snap = json.loads(LAST_GOOD_SNAPSHOT.read_text(encoding="utf-8"))
        if snap.get("items"):
            return snap["as_of"], [Chokepoint(**c) for c in snap["items"]]
    raise RuntimeError(f"PortWatch unavailable and no snapshot to fall back on: {err}")
