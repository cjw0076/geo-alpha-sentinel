"""UN Comtrade public preview live data source.

Fetches bilateral import dependency data, partner area lookups, and computes import HHI.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from sentinel.http import fetch_json
from sentinel.models import Provenance

PARTNER_AREAS_URL = "https://comtradeapi.un.org/files/v1/app/reference/partnerAreas.json"
COMTRADE_PREVIEW_URL = "https://comtradeapi.un.org/public/v1/preview/C/A/HS"

HS_WATCHLIST: Dict[str, str] = {
    "8542": "Integrated circuits",
    "8541": "Semiconductor devices",
    "8486": "Semiconductor manufacturing equipment",
    "8507": "Electric accumulators (batteries)",
    "8505": "Permanent magnets",
    "2846": "Rare-earth compounds",
    "8504": "Electrical transformers",
    "7403": "Refined copper",
    "2825": "Gallium/germanium oxides & other inorganic bases",
}

_partner_cache: Optional[Dict[int, str]] = None


def _get_partner_map() -> Dict[int, str]:
    global _partner_cache
    if _partner_cache is None:
        data, _ = fetch_json(PARTNER_AREAS_URL, ttl=30 * 86400)
        results = data.get("results", [])
        _partner_cache = {}
        for p in results:
            if "id" in p and "text" in p:
                pid = int(p["id"])
                ptext = str(p.get("text", "")).strip()
                # UN Comtrade convention: partnerCode 490 ('Other Asia, nes') represents Taiwan
                if pid == 490 or ptext == "Other Asia, nes":
                    ptext = "Taiwan (Other Asia, nes)"
                _partner_cache[pid] = ptext
    return _partner_cache


def import_dependency(hs: str, reporter: int = 842) -> Dict[str, Any]:
    """Fetch live bilateral import trade data for a given HS code and reporter country.

    Returns:
      {
        "hs": hs,
        "description": desc,
        "year": year,
        "reporter": reporter,
        "total_usd": total_usd,
        "partners": [{"partner": name, "value_usd": val, "share_pct": pct}],  # top 10, excl World
        "hhi": hhi,
        "provenance": Provenance
      }
    """
    clean_hs = hs.strip()
    partner_map = _get_partner_map()
    desc = HS_WATCHLIST.get(clean_hs, f"HS {clean_hs}")

    # Try 2024 first, then 2023 fallback
    year = 2024
    url_2024 = f"{COMTRADE_PREVIEW_URL}?reporterCode={reporter}&period=2024&cmdCode={clean_hs}&flowCode=M"
    data, retrieved_at = fetch_json(url_2024, ttl=7 * 86400)
    records = data.get("data", [])

    if not records:
        year = 2023
        url_2023 = f"{COMTRADE_PREVIEW_URL}?reporterCode={reporter}&period=2023&cmdCode={clean_hs}&flowCode=M"
        data, retrieved_at = fetch_json(url_2023, ttl=7 * 86400)
        records = data.get("data", [])

    world_val = 0.0
    partners_all: List[Dict[str, Any]] = []

    for r in records:
        p_code = int(r.get("partnerCode", 0))
        val = float(r.get("primaryValue", 0.0) or 0.0)
        if p_code == 0:
            world_val = val
        else:
            p_name = partner_map.get(p_code, f"Country {p_code}")
            partners_all.append({"partner": p_name, "value_usd": round(val, 2)})

    total_usd = world_val if world_val > 0.0 else sum(p["value_usd"] for p in partners_all)

    # Sort descending by value_usd
    partners_all.sort(key=lambda x: x["value_usd"], reverse=True)

    # Calculate share_pct for all partners to compute total HHI
    all_shares: List[float] = []
    for p in partners_all:
        pct = round((p["value_usd"] / total_usd) * 100.0, 2) if total_usd > 0.0 else 0.0
        p["share_pct"] = pct
        all_shares.append(pct)

    hhi = round(sum(s * s for s in all_shares), 1)
    top_10 = partners_all[:10]

    prov = Provenance(
        source="un_comtrade",
        title=f"UN Comtrade — HS {clean_hs} ({desc}) Reporter {reporter} ({year})",
        url=f"{COMTRADE_PREVIEW_URL}?reporterCode={reporter}&period={year}&cmdCode={clean_hs}&flowCode=M",
        retrieved_at=retrieved_at,
        license="UN Comtrade — free for non-commercial use with attribution",
        locator=f"HS {clean_hs} (M)",
    )

    return {
        "hs": clean_hs,
        "description": desc,
        "year": year,
        "reporter": reporter,
        "total_usd": round(total_usd, 2),
        "partners": top_10,
        "hhi": hhi,
        "provenance": prov,
    }
