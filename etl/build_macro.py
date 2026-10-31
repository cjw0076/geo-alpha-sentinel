"""ETL pipeline: builds the macro snapshot from the World Bank Pink Sheet (CC BY 4.0) and NY Fed GSCPI.

Output: data/macro.json
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import openpyxl

from sentinel.http import fetch_bytes, utcnow_iso
from sentinel.models import MacroSeries, Provenance
from sentinel.sources.macro import pink_sheet_series

DATA_DIR = ROOT / "data"

GSCPI_URL = (
    "https://www.newyorkfed.org/medialibrary/research/interactives/gscpi/downloads/gscpi_data.xlsx"
)


def parse_gscpi() -> List[Dict[str, Any]]:
    xlsx_bytes, _ = fetch_bytes(GSCPI_URL, ttl=7 * 86400)
    with tempfile.TemporaryDirectory() as td:
        in_file = Path(td) / "gscpi_data.xls"
        in_file.write_bytes(xlsx_bytes)

        # NY Fed serves .xls; convert to .xlsx with LibreOffice for openpyxl
        subprocess.check_call(
            ["libreoffice", "--headless", "--convert-to", "xlsx", str(in_file), "--outdir", td],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        converted_file = Path(td) / "gscpi_data.xlsx"
        wb = openpyxl.load_workbook(str(converted_file))
        ws = wb["GSCPI Monthly Data"]
        rows = list(ws.iter_rows(values_only=True))

    pts: List[Dict[str, Any]] = []
    for r in rows:
        if r[0] and isinstance(r[0], str) and r[1] is not None:
            d_str = r[0].strip()
            try:
                try:
                    dt = datetime.strptime(d_str, "%d-%b-%Y")
                except ValueError:
                    dt = datetime.strptime(d_str, "%Y-%m-%d")
                val = float(r[1])
                pts.append({"date": dt.strftime("%Y-%m-%d"), "value": round(val, 4)})
            except Exception:
                pass
    return pts


def compute_yoy(pts_60m: List[Dict[str, Any]]) -> Optional[float]:
    if len(pts_60m) < 13:
        return None
    latest = pts_60m[-1]["value"]
    val_12m_ago = pts_60m[-13]["value"]
    if val_12m_ago != 0:
        return round(((latest / val_12m_ago) - 1.0) * 100.0, 2)
    return None


def build_macro() -> Dict[str, Any]:
    series_list: List[MacroSeries] = []

    # 1. World Bank Pink Sheet commodity prices (monthly)
    series_list.extend(pink_sheet_series(ttl=86400))

    # 2. Fetch GSCPI
    gscpi_pts = parse_gscpi()
    gscpi_60m = gscpi_pts[-60:] if len(gscpi_pts) >= 60 else gscpi_pts
    if gscpi_60m:
        latest_gscpi = gscpi_60m[-1]
        # GSCPI oscillates around zero standard deviations; YoY percentage is non-meaningful.
        yoy_gscpi = None
        prov_gscpi = Provenance(
            source="nyfed_gscpi",
            title="NY Fed Global Supply Chain Pressure Index",
            url="https://www.newyorkfed.org/research/gscpi.html",
            retrieved_at=utcnow_iso(),
            license="Federal Reserve Bank of New York — free use with attribution",
            locator="GSCPI Monthly Data",
        )
        series_list.append(
            MacroSeries(
                id="GSCPI",
                name="Global Supply Chain Pressure Index (GSCPI)",
                unit="Standard deviations from average value",
                latest_date=latest_gscpi["date"],
                latest_value=latest_gscpi["value"],
                yoy_pct=None,
                series=gscpi_60m,
                provenance=prov_gscpi,
            )
        )

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_file = DATA_DIR / "macro.json"
    payload = {
        "generated_at": utcnow_iso(),
        "count": len(series_list),
        "items": [
            s.model_dump() if hasattr(s, "model_dump") else s.dict()
            for s in series_list
        ],
    }
    out_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"Wrote {len(series_list)} macro series to {out_file}")
    return payload


if __name__ == "__main__":
    build_macro()
