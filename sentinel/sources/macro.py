"""Macroeconomic series: World Bank commodity prices (Pink Sheet) and NY Fed GSCPI.

Commodity prices come from the World Bank's monthly Pink Sheet (CC BY 4.0), which
permits storing and redistributing with attribution. The committed snapshot in
data/macro.json is refreshed by etl/build_macro.py; get_macro() refreshes the
commodity block live (weekly cache) when the workbook can be read, else serves
the snapshot.
"""

from __future__ import annotations

import io
import json
from typing import Any, Dict, List, Optional, Tuple

from sentinel.config import DATA_DIR
from sentinel.http import fetch_bytes
from sentinel.models import MacroSeries, Provenance

MACRO_JSON_PATH = DATA_DIR / "macro.json"

PINK_SHEET_PAGE = "https://www.worldbank.org/en/research/commodity-markets"
PINK_SHEET_XLSX = (
    "https://thedocs.worldbank.org/en/doc/74e8be41ceb20fa0da750cda2f6b9e4e-0050012026/related/"
    "CMO-Historical-Data-Monthly.xlsx"
)
PINK_SHEET_LICENSE = "CC BY 4.0 — World Bank Commodity Price Data (The Pink Sheet)"

# id, Pink Sheet column header (sheet "Monthly Prices"), display name, unit
WB_SERIES = [
    ("WB_BRENT", "Crude oil, Brent", "Crude oil, Brent", "USD/bbl"),
    ("WB_NGAS_EUR", "Natural gas, Europe", "Natural gas, Europe (TTF)", "USD/MMBtu"),
    ("WB_COPPER", "Copper", "Copper", "USD/mt"),
    ("WB_ALUMINUM", "Aluminum", "Aluminum", "USD/mt"),
    ("WB_NICKEL", "Nickel", "Nickel", "USD/mt"),
]


def _compute_yoy(pts_60m: List[Dict[str, Any]]) -> Optional[float]:
    if len(pts_60m) < 13:
        return None
    latest = pts_60m[-1]["value"]
    val_12m_ago = pts_60m[-13]["value"]
    if val_12m_ago != 0:
        return round(((latest / val_12m_ago) - 1.0) * 100.0, 2)
    return None


def _month_end(period: str) -> str:
    """'2026M09' -> '2026-09-30'."""
    import calendar

    y, mth = int(period[:4]), int(period[5:7])
    return f"{y:04d}-{mth:02d}-{calendar.monthrange(y, mth)[1]:02d}"


def parse_pink_sheet(xlsx_bytes: bytes) -> Tuple[str, Dict[str, List[Dict[str, Any]]]]:
    """Return (updated_note, {series_id: [{date, value}, ...]}) from the monthly workbook."""
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), read_only=True, data_only=True)
    rows = list(wb["Monthly Prices"].iter_rows(values_only=True))
    updated = next((str(r[0]) for r in rows[:6] if r and r[0] and str(r[0]).startswith("Updated")), "")
    header_row = next(i for i, r in enumerate(rows) if r and "Crude oil, Brent" in r)
    header = rows[header_row]
    col = {name: header.index(name) for _, name, _, _ in WB_SERIES}
    out: Dict[str, List[Dict[str, Any]]] = {sid: [] for sid, *_ in WB_SERIES}
    for r in rows[header_row + 2:]:
        period = r[0]
        if not (isinstance(period, str) and len(period) == 7 and period[4] == "M"):
            continue
        for sid, name, _, _ in WB_SERIES:
            v = r[col[name]]
            if isinstance(v, (int, float)):
                out[sid].append({"date": _month_end(period), "value": round(float(v), 2)})
    return updated, out


def pink_sheet_series(ttl: int = 7 * 86400) -> List[MacroSeries]:
    raw, retrieved_at = fetch_bytes(PINK_SHEET_XLSX, ttl=ttl, headers={"User-Agent": "Mozilla/5.0"})
    updated, data = parse_pink_sheet(raw)
    series: List[MacroSeries] = []
    for sid, col_name, name, unit in WB_SERIES:
        pts = data[sid][-60:]
        if not pts:
            continue
        series.append(MacroSeries(
            id=sid, name=name, unit=unit,
            latest_date=pts[-1]["date"], latest_value=pts[-1]["value"],
            yoy_pct=_compute_yoy(pts), series=pts,
            provenance=Provenance(
                source="worldbank_cmo",
                title=f"World Bank Pink Sheet — {name} (monthly){' · ' + updated if updated else ''}",
                url=PINK_SHEET_PAGE, retrieved_at=retrieved_at, license=PINK_SHEET_LICENSE,
                locator=f"CMO-Historical-Data-Monthly.xlsx › Monthly Prices › {col_name}",
            ),
        ))
    return series


def _load_snapshot() -> Dict[str, MacroSeries]:
    if not MACRO_JSON_PATH.exists():
        return {}
    try:
        data = json.loads(MACRO_JSON_PATH.read_text(encoding="utf-8"))
        return {item["id"]: MacroSeries(**item) for item in data.get("items", [])}
    except Exception:
        return {}


def get_macro() -> List[MacroSeries]:
    """World Bank commodity series (live weekly cache, snapshot fallback) followed by GSCPI (snapshot)."""
    snapshot = _load_snapshot()
    try:
        live = {s.id: s for s in pink_sheet_series()}
    except Exception:
        live = {}
    results = [live.get(sid) or snapshot.get(sid) for sid, *_ in WB_SERIES]
    results = [s for s in results if s is not None]
    gscpi = snapshot.get("GSCPI")
    if gscpi:
        gscpi.yoy_pct = None  # the index oscillates around zero; a % change is not meaningful
        results.append(gscpi)
    return results
