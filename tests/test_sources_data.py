"""Unit and integration tests for data sources, ETL snapshots, and screening pipelines.

Offline tests run against committed snapshots in data/.
Network tests are marked with @pytest.mark.network.
"""

from __future__ import annotations

import gzip
import json
import time
from pathlib import Path

import pytest

from sentinel.config import DATA_DIR
from sentinel.models import MacroSeries, Mineral, SanctionsScreen
from sentinel.sources.comtrade import HS_WATCHLIST, import_dependency
from sentinel.sources.macro import get_macro
from sentinel.sources.minerals import find_minerals, get_minerals
from sentinel.sources.portwatch import CHOKEPOINT_ALIASES, get_chokepoints
from sentinel.sources.sanctions import screen, screen_many

MINERALS_JSON_PATH = DATA_DIR / "minerals_usgs_mcs2026.json"
CSL_GZ_PATH = DATA_DIR / "csl_entities.json.gz"
MACRO_JSON_PATH = DATA_DIR / "macro.json"


# ---------------------------------------------------------------------------
# 1. Offline Tests: USGS MCS 2026 Minerals
# ---------------------------------------------------------------------------

def test_minerals_snapshot_exists_and_parses():
    assert MINERALS_JSON_PATH.exists(), f"Missing {MINERALS_JSON_PATH}"
    data = json.loads(MINERALS_JSON_PATH.read_text(encoding="utf-8"))
    assert data.get("edition") == "MCS 2026"
    items = data.get("items", [])
    assert len(items) >= 20

    # Ensure every item parses into the pydantic Mineral model
    for item in items:
        m = Mineral(**item)
        assert m.id
        assert m.name
        assert m.year == 2025
        assert m.measure
        assert m.unit
        assert len(m.countries) >= 2
        assert m.provenance.source == "usgs_mcs"


def test_minerals_hhi_recompute():
    edition, minerals = get_minerals()
    assert edition == "MCS 2026"
    assert len(minerals) >= 20

    for m in minerals:
        shares = [c.share_pct for c in m.countries if c.share_pct is not None]
        assert len(shares) > 0
        computed_hhi = sum(s * s for s in shares)
        # HHI in model is rounded to 1 decimal place; check within 1.0 margin
        assert abs(computed_hhi - (m.hhi or 0.0)) < 1.0


def test_minerals_spotchecks():
    edition, minerals = get_minerals()
    by_id = {m.id: m for m in minerals}

    # 1. Rare Earths: China is top country and largest share (>60%)
    re_obj = by_id["rare-earths"]
    assert re_obj.top_country == "China"
    assert re_obj.top_share_pct is not None and re_obj.top_share_pct > 60.0
    assert re_obj.china_share_pct is not None and re_obj.china_share_pct > 60.0

    # 2. Gallium: China share > 90%
    ga_obj = by_id["gallium"]
    assert ga_obj.top_country == "China"
    assert ga_obj.china_share_pct is not None and ga_obj.china_share_pct >= 90.0

    # 3. Lithium: Australia is top country
    li_obj = by_id["lithium"]
    assert li_obj.top_country == "Australia"
    assert li_obj.china_share_pct is not None

    # 4. Cobalt: Congo is top country
    co_obj = by_id["cobalt"]
    assert co_obj.top_country == "Congo (Kinshasa)"

    # 5. Exclude "Other countries" from top_country
    for m in minerals:
        assert m.top_country not in ("Other", "Other countries")


def test_minerals_find_and_aliases():
    matched = find_minerals(["rare earth", "gallium", "copper", "pgm", "ilmenite"])
    assert len(matched) == 5
    ids = [m.id for m in matched]
    assert "rare-earths" in ids
    assert "gallium" in ids
    assert "copper" in ids
    assert "platinum-group" in ids
    assert "titanium" in ids


# ---------------------------------------------------------------------------
# 2. Offline Tests: Trade.gov CSL Sanction Screening
# ---------------------------------------------------------------------------

def test_sanctions_snapshot_size_and_count():
    assert CSL_GZ_PATH.exists()
    gz_mb = CSL_GZ_PATH.stat().st_size / (1024 * 1024)
    assert gz_mb < 4.0, f"Snapshot too large: {gz_mb:.2f} MB"

    with gzip.open(CSL_GZ_PATH, "rt", encoding="utf-8") as f:
        data = json.load(f)
    assert data.get("count", 0) > 15000
    assert len(data.get("entities", [])) > 15000


def test_sanctions_screen_cases():
    # 1. Huawei: matches Entity List
    r_huawei = screen("Huawei Technologies")
    assert isinstance(r_huawei, SanctionsScreen)
    assert len(r_huawei.matches) >= 1
    assert any("Entity List" in m.list_source for m in r_huawei.matches)
    top_huawei = r_huawei.matches[0]
    assert top_huawei.score >= 0.86

    # 2. NVIDIA: zero matches
    r_nvda = screen("NVIDIA")
    assert len(r_nvda.matches) == 0

    # 3. SMIC: returns Semiconductor Manufacturing International Corporation
    r_smic = screen("Semiconductor Manufacturing International Corporation")
    assert len(r_smic.matches) >= 1
    assert any("Semiconductor Manufacturing International" in m.name for m in r_smic.matches)
    assert r_smic.matches[0].score >= 0.90


def test_sanctions_screen_performance():
    # Warmup
    screen("Warmup Entity")

    # Benchmark query latency < 300ms
    t0 = time.time()
    screen("Huawei Technologies")
    dt_ms = (time.time() - t0) * 1000.0
    assert dt_ms < 300.0, f"Query took too long: {dt_ms:.1f}ms"


def test_sanctions_screen_many():
    results = screen_many(["Huawei Technologies", "NVIDIA", "Tesla Inc"])
    assert len(results) == 3
    assert len(results[0].matches) >= 1
    assert len(results[1].matches) == 0


# ---------------------------------------------------------------------------
# 3. Offline Tests: FRED & GSCPI Macro Series
# ---------------------------------------------------------------------------

def test_macro_snapshot_schema_and_contents():
    assert MACRO_JSON_PATH.exists()
    data = json.loads(MACRO_JSON_PATH.read_text(encoding="utf-8"))
    items = data.get("items", [])
    assert len(items) == 6

    ids = [item["id"] for item in items]
    expected_ids = ["PCOPPUSDM", "PALUMUSDM", "PNICKUSDM", "DCOILBRENTEU", "PNGASEUUSDM", "GSCPI"]
    for eid in expected_ids:
        assert eid in ids

    for item in items:
        s = MacroSeries(**item)
        assert s.latest_date
        assert s.latest_value != 0
        assert len(s.series) <= 60
        assert s.provenance.license


def test_macro_source_module():
    series = get_macro()
    assert len(series) == 6
    by_id = {s.id: s for s in series}

    # Verify GSCPI
    gscpi = by_id["GSCPI"]
    assert gscpi.name == "Global Supply Chain Pressure Index (GSCPI)"
    assert gscpi.latest_date >= "2026-01-01"

    # Verify Copper
    copper = by_id["PCOPPUSDM"]
    assert copper.unit == "USD/mt"
    assert copper.latest_value > 5000.0


# ---------------------------------------------------------------------------
# 4. Network Tests (IMF PortWatch, UN Comtrade, Live FRED)
# ---------------------------------------------------------------------------

@pytest.mark.network
def test_portwatch_live():
    as_of, chokepoints = get_chokepoints()
    assert len(chokepoints) == 28
    assert as_of >= "2026-09-01"

    # Verify key chokepoint aliases
    by_id = {c.id: c for c in chokepoints}
    assert "chokepoint11" in by_id  # Taiwan Strait
    assert "chokepoint5" in by_id   # Malacca Strait
    assert "chokepoint6" in by_id   # Strait of Hormuz

    taiwan = by_id["chokepoint11"]
    assert taiwan.name == "Taiwan Strait"
    assert taiwan.latest is not None
    assert taiwan.latest.n_total > 0
    assert len(taiwan.series) >= 30
    assert taiwan.delta_pct is not None


@pytest.mark.network
def test_comtrade_live():
    dep = import_dependency("8542", reporter=842)
    assert dep["hs"] == "8542"
    assert dep["total_usd"] > 1_000_000_000.0  # > $1B
    assert len(dep["partners"]) == 10
    assert dep["hhi"] > 100.0
    assert dep["provenance"].source == "un_comtrade"
    # Ensure partnerCode 0 is excluded
    assert not any(p["partner"] == "World" for p in dep["partners"])
