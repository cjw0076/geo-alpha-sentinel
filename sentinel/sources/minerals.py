"""USGS Mineral Commodity Summaries (MCS 2026) data source.

Loads structured mineral data from data/minerals_usgs_mcs2026.json.
Provides fast case-insensitive search and access to 4W critical minerals.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import List, Tuple

from sentinel.config import DATA_DIR
from sentinel.models import Mineral

MINERALS_JSON_PATH = DATA_DIR / "minerals_usgs_mcs2026.json"

# Common alias mapping for commodity search
MINERAL_ALIASES = {
    "rare earth": "rare-earths",
    "rare earths": "rare-earths",
    "rare-earth": "rare-earths",
    "ree": "rare-earths",
    "pgm": "platinum-group",
    "platinum": "platinum-group",
    "palladium": "platinum-group",
    "titanium": "titanium",
    "ilmenite": "titanium",
    "magnesium": "magnesium",
    "bauxite": "aluminum",
}


@lru_cache(maxsize=1)
def _load_cached_minerals() -> Tuple[str, List[Mineral]]:
    if not MINERALS_JSON_PATH.exists():
        raise FileNotFoundError(
            f"Minerals snapshot not found at {MINERALS_JSON_PATH}. Run etl/build_minerals.py first."
        )
    data = json.loads(MINERALS_JSON_PATH.read_text(encoding="utf-8"))
    edition = str(data.get("edition", "MCS 2026"))
    items = [Mineral(**item) for item in data.get("items", [])]
    return edition, items


def get_minerals() -> Tuple[str, List[Mineral]]:
    """Return (edition, items) from the USGS MCS 2026 snapshot."""
    return _load_cached_minerals()


def find_minerals(names: List[str]) -> List[Mineral]:
    """Find minerals by case-insensitive name or id matching.

    Supports aliases like 'rare earth' -> 'rare-earths', 'pgm' -> 'platinum-group'.
    """
    _, all_minerals = get_minerals()
    index_by_id = {m.id.lower(): m for m in all_minerals}
    index_by_name = {m.name.lower(): m for m in all_minerals}

    matched: List[Mineral] = []
    seen_ids = set()

    for query in names:
        q_norm = query.strip().lower()
        if not q_norm:
            continue

        # Check alias first
        target_id = MINERAL_ALIASES.get(q_norm, q_norm)

        m = index_by_id.get(target_id)
        if not m:
            m = index_by_name.get(q_norm)
        if not m:
            # Substring match if exact match not found
            for min_obj in all_minerals:
                if q_norm in min_obj.id.lower() or q_norm in min_obj.name.lower():
                    m = min_obj
                    break

        if m and m.id not in seen_ids:
            seen_ids.add(m.id)
            matched.append(m)

    return matched
