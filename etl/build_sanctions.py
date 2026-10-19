"""ETL pipeline: downloads and compacts Trade.gov Consolidated Screening List (CSL).

Source: https://data.trade.gov/downloadable_consolidated_screening_list/v1/consolidated.csv
Output: data/csl_entities.json.gz (< 4 MB)
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sentinel.http import fetch_bytes, utcnow_iso

CSL_URL = "https://data.trade.gov/downloadable_consolidated_screening_list/v1/consolidated.csv"
DATA_DIR = ROOT / "data"

TOP_COUNTRY_CODES: Dict[str, str] = {
    "CN": "China",
    "RU": "Russia",
    "IR": "Iran",
    "CU": "Cuba",
    "KP": "North Korea",
    "SY": "Syria",
    "VE": "Venezuela",
    "BY": "Belarus",
    "MM": "Burma",
    "HK": "Hong Kong",
    "US": "United States",
    "GB": "United Kingdom",
    "AE": "United Arab Emirates",
    "TR": "Turkey",
    "CY": "Cyprus",
    "SG": "Singapore",
    "CH": "Switzerland",
    "DE": "Germany",
    "NL": "Netherlands",
    "VG": "British Virgin Islands",
    "PA": "Panama",
    "LB": "Lebanon",
    "UA": "Ukraine",
    "TW": "Taiwan",
    "IN": "India",
    "KR": "South Korea",
    "JP": "Japan",
    "CA": "Canada",
    "MX": "Mexico",
    "IL": "Israel",
    "ZA": "South Africa",
    "BR": "Brazil",
    "VN": "Vietnam",
    "TH": "Thailand",
    "MY": "Malaysia",
    "ID": "Indonesia",
    "PK": "Pakistan",
    "AF": "Afghanistan",
    "IQ": "Iraq",
    "YE": "Yemen",
}


def build_sanctions() -> Dict[str, Any]:
    print(f"Fetching CSL CSV from {CSL_URL}...")
    csv_bytes, retrieved_at = fetch_bytes(CSL_URL, ttl=7 * 86400)
    print(f"Downloaded {len(csv_bytes)} bytes.")

    reader = csv.DictReader(io.StringIO(csv_bytes.decode("utf-8", errors="replace")))
    entities: List[Dict[str, Any]] = []

    for r in reader:
        # Keep only type in ('Entity', '') and exclude vessels
        t = r.get("type", "").strip()
        if t not in ("Entity", ""):
            continue
        if r.get("vessel_type", "").strip() or r.get("call_sign", "").strip():
            continue

        name = r.get("name", "").strip()
        if not name:
            continue

        alts_raw = r.get("alt_names", "").strip()
        alts = [a.strip() for a in alts_raw.split(";") if a.strip()] if alts_raw else []

        # Extract country code from last address segment
        addr = r.get("addresses", "").strip()
        country: Optional[str] = None
        if addr:
            first_addr = addr.split(";")[0].strip()
            m_cc = re.search(r"[\s,]([A-Z]{2})\s*$", first_addr)
            if m_cc:
                code = m_cc.group(1)
                country = TOP_COUNTRY_CODES.get(code, code)

        entities.append({
            "n": name,
            "a": alts,
            "s": r.get("source", "").strip(),
            "p": r.get("programs", "").strip() or None,
            "c": country,
            "e": r.get("entity_number", "").strip() or None,
            "u": r.get("source_list_url", "").strip() or None,
        })

    snapshot_date = retrieved_at[:10]
    payload = {
        "snapshot_date": snapshot_date,
        "source_url": CSL_URL,
        "count": len(entities),
        "entities": entities,
    }

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DATA_DIR / "csl_entities.json.gz"
    json_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    with gzip.open(out_path, "wb", compresslevel=6) as f:
        f.write(json_bytes)

    out_mb = out_path.stat().st_size / (1024 * 1024)
    print(f"Wrote {len(entities)} entities to {out_path} ({out_mb:.2f} MB)")
    return payload


if __name__ == "__main__":
    build_sanctions()
