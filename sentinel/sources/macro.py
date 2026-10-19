"""Macroeconomic data sources: FRED commodity prices & NY Fed GSCPI.

Provides live FRED series (with fallback to data/macro.json snapshot)
and GSCPI from the committed snapshot.
"""

from __future__ import annotations

import csv
import io
import json
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from sentinel.config import DATA_DIR
from sentinel.http import fetch_bytes, utcnow_iso
from sentinel.models import MacroSeries, Provenance

MACRO_JSON_PATH = DATA_DIR / "macro.json"

FRED_CONFIGS = [
    {
        "id": "PCOPPUSDM",
        "name": "Global Price of Copper",
        "unit": "USD/mt",
        "url": "https://fred.stlouisfed.org/graph/fredgraph.csv?id=PCOPPUSDM",
        "monthly": True,
    },
    {
        "id": "PALUMUSDM",
        "name": "Global Price of Aluminum",
        "unit": "USD/mt",
        "url": "https://fred.stlouisfed.org/graph/fredgraph.csv?id=PALUMUSDM",
        "monthly": True,
    },
    {
        "id": "PNICKUSDM",
        "name": "Global Price of Nickel",
        "unit": "USD/mt",
        "url": "https://fred.stlouisfed.org/graph/fredgraph.csv?id=PNICKUSDM",
        "monthly": True,
    },
    {
        "id": "DCOILBRENTEU",
        "name": "Crude Oil Prices: Brent - Europe",
        "unit": "USD/bbl",
        "url": "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DCOILBRENTEU",
        "monthly": False,
    },
    {
        "id": "PNGASEUUSDM",
        "name": "Global Price of Natural Gas, EU",
        "unit": "USD/MMBtu",
        "url": "https://fred.stlouisfed.org/graph/fredgraph.csv?id=PNGASEUUSDM",
        "monthly": True,
    },
]


def _compute_yoy(pts_60m: List[Dict[str, Any]]) -> Optional[float]:
    if len(pts_60m) < 13:
        return None
    latest = pts_60m[-1]["value"]
    val_12m_ago = pts_60m[-13]["value"]
    if val_12m_ago != 0:
        return round(((latest / val_12m_ago) - 1.0) * 100.0, 2)
    return None


def _parse_fred_csv(csv_bytes: bytes, monthly: bool) -> List[Dict[str, Any]]:
    reader = csv.reader(io.StringIO(csv_bytes.decode("utf-8", errors="replace")))
    next(reader, None)

    if monthly:
        pts: List[Dict[str, Any]] = []
        for row in reader:
            if len(row) >= 2 and row[1].strip() not in (".", "", "NA"):
                try:
                    d, val = row[0].strip(), float(row[1].strip())
                    pts.append({"date": d, "value": round(val, 2)})
                except ValueError:
                    pass
        return pts
    else:
        # Daily: resample to month-end mean
        by_month: Dict[str, List[Tuple[str, float]]] = defaultdict(list)
        for row in reader:
            if len(row) >= 2 and row[1].strip() not in (".", "", "NA"):
                try:
                    d, val = row[0].strip(), float(row[1].strip())
                    ym = d[:7]
                    by_month[ym].append((d, val))
                except ValueError:
                    pass

        resampled: List[Dict[str, Any]] = []
        for ym in sorted(by_month.keys()):
            month_pts = by_month[ym]
            last_date = month_pts[-1][0]
            mean_val = float(np.mean([p[1] for p in month_pts]))
            resampled.append({"date": last_date, "value": round(mean_val, 2)})
        return resampled


def _load_snapshot() -> Dict[str, MacroSeries]:
    if not MACRO_JSON_PATH.exists():
        return {}
    try:
        data = json.loads(MACRO_JSON_PATH.read_text(encoding="utf-8"))
        return {item["id"]: MacroSeries(**item) for item in data.get("items", [])}
    except Exception:
        return {}


def get_macro() -> List[MacroSeries]:
    """Return all 6 MacroSeries: FRED live (ttl 24h) with snapshot fallback, and GSCPI from snapshot."""
    snapshot = _load_snapshot()
    results: List[MacroSeries] = []

    # 1. FRED series (live with fallback)
    for cfg in FRED_CONFIGS:
        sid = cfg["id"]
        series_obj: Optional[MacroSeries] = None
        try:
            csv_bytes, retrieved_at = fetch_bytes(cfg["url"], ttl=24 * 3600)
            pts = _parse_fred_csv(csv_bytes, cfg["monthly"])
            pts_60m = pts[-60:] if len(pts) >= 60 else pts
            if pts_60m:
                latest = pts_60m[-1]
                yoy = _compute_yoy(pts_60m)
                prov = Provenance(
                    source="fred",
                    title=f"FRED — {cfg['name']} ({sid})",
                    url=cfg["url"],
                    retrieved_at=retrieved_at,
                    license="Public domain/IMF Primary Commodity Prices via FRED",
                    locator=sid,
                )
                series_obj = MacroSeries(
                    id=sid,
                    name=cfg["name"],
                    unit=cfg["unit"],
                    latest_date=latest["date"],
                    latest_value=latest["value"],
                    yoy_pct=yoy,
                    series=pts_60m,
                    provenance=prov,
                )
        except Exception:
            # Fallback to snapshot
            series_obj = snapshot.get(sid)

        if not series_obj and sid in snapshot:
            series_obj = snapshot[sid]
        if series_obj:
            results.append(series_obj)

    # 2. GSCPI from snapshot only (yoy_pct is None because index oscillates around zero)
    gscpi_obj = snapshot.get("GSCPI")
    if gscpi_obj:
        gscpi_obj.yoy_pct = None
        results.append(gscpi_obj)

    return results
