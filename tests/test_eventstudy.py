"""Offline tests for the event-validation harness (sentinel/analytics/eventstudy.py, sec.annual_filing_before)."""

import json
import math
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

from sentinel.analytics import eventstudy as E
from sentinel.sources import sec

ROOT = Path(__file__).resolve().parent.parent
EVENTS_JSON = ROOT / "public" / "snapshots" / "validation" / "events.json"


# ---- declaration -------------------------------------------------------------

def test_events_declared_and_well_formed():
    assert len(E.EVENTS) == 7
    ids = [e["id"] for e in E.EVENTS]
    assert len(set(ids)) == len(ids)
    for e in E.EVENTS:
        assert e["channel"] in {"taiwan", "china_export", "hormuz", "malacca", "weaponized"}
        assert e["expected_sign"] == "negative"
        assert e["source_url"].startswith("http")
        assert date.fromisoformat(e["date"]).weekday() < 5  # t=0 is a weekday
        assert e["mention_regions"]
    assert E.WINDOWS == {"primary": (-1, 5), "secondary": (-1, 20), "estimation": (-260, -30)}


# ---- market model and CAR ----------------------------------------------------

def test_market_model_recovers_alpha_beta():
    rng = np.random.default_rng(0)
    r_m = rng.normal(0, 0.01, 400)
    r_i = 0.0007 + 1.3 * r_m + rng.normal(0, 0.001, 400)
    a, b, n = E.market_model(r_i, r_m)
    assert n == 400
    assert abs(b - 1.3) < 0.02
    assert abs(a - 0.0007) < 0.0002


def test_market_model_requires_min_obs():
    r = np.full(200, np.nan)
    r[:119] = np.linspace(-0.01, 0.01, 119)
    with pytest.raises(E.InsufficientData):
        E.market_model(r, r)


def test_car_sums_abnormal_returns_and_window_indexing():
    rng = np.random.default_rng(1)
    n = 320
    r_m = rng.normal(0, 0.01, n)
    r_i = 0.0005 + 0.8 * r_m
    t0 = 290
    bump = np.zeros(n)
    bump[t0 - 1 : t0 + 6] = 0.01  # +1% abnormal on each of the 7 primary-window days
    out = E.event_cars(r_i + bump, r_m, t0)
    assert abs(out["beta"] - 0.8) < 1e-9 and abs(out["alpha"] - 0.0005) < 1e-9
    assert abs(out["primary"] - 0.07) < 1e-9
    assert abs(out["secondary"] - 0.07) < 1e-9
    assert out["n_est"] == 231
    ar = E.abnormal_returns(r_i + bump, r_m, out["alpha"], out["beta"])
    assert abs(E.car(ar[t0 - 1 : t0 + 6]) - 0.07) < 1e-9
    assert math.isnan(E.car(np.array([0.01, np.nan])))


def test_aligned_log_returns_and_event_index():
    cal = ["2023-06-28", "2023-06-29", "2023-06-30", "2023-07-03", "2023-07-05"]
    px = [("2023-06-28", 100.0), ("2023-06-29", 110.0), ("2023-07-03", 121.0), ("2023-07-05", 121.0)]
    r = E.aligned_log_returns(px, cal)
    assert math.isnan(r[0]) and abs(r[1] - math.log(1.1)) < 1e-12
    assert math.isnan(r[2]) and math.isnan(r[3])  # missing 06-30 breaks both adjacent returns
    assert r[4] == 0.0
    assert E.event_index(cal, "2023-07-01") == 3


# ---- cross-sectional statistics ----------------------------------------------

def test_spearman_ic_and_t_hand_computed():
    x = [1, 2, 3, 4, 5]
    y = [5, 6, 7, 8, 7]  # ranks 1, 2, 3.5, 5, 3.5
    ic = E.spearman(x, y)
    assert abs(ic - 8 / math.sqrt(95)) < 1e-12  # 0.82078
    t = E.ic_tstat(ic, 5)
    assert abs(t - ic * math.sqrt(3 / (1 - ic * ic))) < 1e-12
    assert abs(t - 2.48868) < 1e-4  # 8/sqrt(95) * sqrt(3 / (31/95))
    assert E.spearman([1, 2, 3], [3, 2, 1]) == pytest.approx(-1.0)


def test_t_distribution_p_values():
    assert E.t_two_sided_p(2.0, 10) == pytest.approx(0.07339, abs=1e-4)
    assert E.t_two_sided_p(1.812461, 10) == pytest.approx(0.10, abs=1e-4)
    assert E.t_two_sided_p(0.0, 30) == pytest.approx(1.0)
    assert E.t_two_sided_p(-2.0, 10) == E.t_two_sided_p(2.0, 10)


def test_quintile_spread_on_toy_set():
    x = np.arange(1, 11, dtype=float)
    q = E.quintiles(x)
    assert q.tolist() == [1, 1, 2, 2, 3, 3, 4, 4, 5, 5]
    cs = E.cross_section(x, x / 100.0, "negative", bootstrap=False)  # CAR in fraction; pp = x
    assert cs["q5q1_pp"] == pytest.approx(9.5 - 1.5)
    assert cs["ic"] == pytest.approx(1.0) and cs["sign_ok"] is False
    a, b = np.array([9.0, 10.0]), np.array([1.0, 2.0])
    assert cs["q5q1_t"] == pytest.approx((a.mean() - b.mean()) / math.sqrt(a.var(ddof=1) / 2 + b.var(ddof=1) / 2))


def test_quintiles_ties_share_bucket():
    q = E.quintiles([0, 0, 0, 0, 0, 0, 1, 2, 3, 4])
    assert len(set(q[:6].tolist())) == 1


def test_bootstrap_ci_is_seeded_and_brackets_ic():
    rng = np.random.default_rng(3)
    x = rng.normal(size=60)
    y = -0.5 * x + rng.normal(size=60)
    lo, hi = E.bootstrap_ic_ci(x, y)
    assert (lo, hi) == E.bootstrap_ic_ci(x, y)
    assert lo < E.spearman(x, y) < hi


def test_sector_baseline_leave_one_out():
    out = E.sector_baseline([1.0, 2.0, 3.0, 10.0], ["36", "36", "73", "28"])
    assert out.tolist() == pytest.approx([2.0, 1.0, 13.0 / 3.0, 2.0])


def test_summary_counts():
    evs = [
        {"n": 50, "ic": -0.3, "sign_ok": True, "significant_10pct": True, "ic_secondary": -0.1, "q5q1_pp": -2.0,
         "baselines_detail": {"sector": {"ic": -0.1, "sign_ok": True, "significant_10pct": False},
                              "mention": {"ic": 0.1, "sign_ok": False, "significant_10pct": False}}},
        {"n": 50, "ic": 0.1, "sign_ok": False, "significant_10pct": False, "ic_secondary": 0.2, "q5q1_pp": 1.0,
         "baselines_detail": {"sector": {"ic": 0.3, "sign_ok": False, "significant_10pct": True},
                              "mention": {"ic": -0.2, "sign_ok": True, "significant_10pct": False}}},
    ]
    s = E.summarize(evs)
    assert s["events_n"] == 2 and s["sign_ok_n"] == 1 and s["significant_10pct_n"] == 1
    assert s["mean_ic"] == pytest.approx(-0.1)
    assert s["baseline_sector_mean_ic"] == pytest.approx(0.1) and s["baseline_sector_significant_10pct_n"] == 1
    assert s["baseline_mention_sign_ok_n"] == 1


def test_portwatch_threshold_rule():
    d0 = date(2025, 1, 1)
    vals = [100.0] * 400 + [10.0] * 10
    series = [((d0 + timedelta(days=i)).isoformat(), v) for i, v in enumerate(vals)]
    hit = E.portwatch_threshold_date(series)
    # 7-day mean after k low days = (7-k)*100+k*10)/7 < 50 first at k = 4
    assert hit["date"] == (d0 + timedelta(days=403)).isoformat()
    assert hit["ratio"] < 0.5


# ---- point-in-time filing selection ------------------------------------------

def _block(rows):
    return {
        "form": [r[0] for r in rows], "filingDate": [r[1] for r in rows],
        "accessionNumber": [f"0000000001-{r[1][2:4]}-{i:06d}" for i, r in enumerate(rows)],
        "primaryDocument": [f"doc{i}.htm" for i in range(len(rows))], "reportDate": [r[1][:4] + "-01-31" for r in rows],
    }


SUB = {
    "filings": {
        "recent": _block([("10-Q", "2023-05-01"), ("10-K/A", "2023-03-01"), ("10-K", "2023-02-20"), ("8-K", "2023-01-10"), ("40-F", "2023-01-05")]),
        "files": [
            {"name": "CIK0000000001-submissions-001.json", "filingFrom": "2019-01-01", "filingTo": "2022-12-31"},
            {"name": "CIK0000000001-submissions-002.json", "filingFrom": "2010-01-01", "filingTo": "2018-12-31"},
        ],
    }
}
PAGES = {
    "CIK0000000001-submissions-001.json": _block([("10-Q", "2022-11-01"), ("10-K", "2022-02-18"), ("10-K/A", "2022-04-01"), ("10-K", "2021-02-19")]),
    "CIK0000000001-submissions-002.json": _block([("10-K", "2018-02-16"), ("10-K", "2017-02-17")]),
}


class FakeFetch:
    def __init__(self):
        self.calls = []

    def __call__(self, url, ttl=0, **kw):
        name = url.rsplit("/", 1)[-1]
        self.calls.append(name)
        return PAGES[name], "2026-10-31T00:00:00Z"


def test_annual_filing_before_recent_block_original_only():
    f = FakeFetch()
    got = sec.annual_filing_before(SUB, "0000000001", "2023-06-01", fetch=f)
    assert got.form == "10-K" and got.filed == "2023-02-20"  # 10-K/A of 03-01 and 40-F ignored
    assert got.url.endswith("/doc2.htm")
    assert f.calls == []  # never pages when `recent` already has a hit


def test_annual_filing_before_pages_older_files_strictly_before():
    f = FakeFetch()
    got = sec.annual_filing_before(SUB, "0000000001", "2023-02-20", fetch=f)  # same-day filing is not "before"
    assert got.filed == "2022-02-18" and got.form == "10-K"
    assert f.calls == ["CIK0000000001-submissions-001.json"]  # page 002 is older than the hit: skipped
    got = sec.annual_filing_before(SUB, "0000000001", "2019-05-16", fetch=FakeFetch())
    assert got.filed == "2018-02-16"


def test_annual_filing_before_age_limit_and_none():
    assert sec.annual_filing_before(SUB, "0000000001", "2019-05-16", max_age_days=550, fetch=FakeFetch()).filed == "2018-02-16"  # 454 days
    assert sec.annual_filing_before(SUB, "0000000001", "2019-05-16", max_age_days=300, fetch=FakeFetch()) is None
    assert sec.annual_filing_before(SUB, "0000000001", "2019-12-01", fetch=FakeFetch()) is None  # 653 days > 550
    assert sec.annual_filing_before(SUB, "0000000001", "2009-01-01", fetch=FakeFetch()) is None


# ---- published JSON holds derived statistics only ----------------------------

def _numeric_lists(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _numeric_lists(v, f"{path}.{k}")
    elif isinstance(obj, list):
        if obj and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in obj):
            yield path, len(obj)
        for i, v in enumerate(obj):
            yield from _numeric_lists(v, f"{path}[{i}]")


def _keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _keys(v)


def test_events_json_has_no_raw_price_arrays():
    if not EVENTS_JSON.exists():
        pytest.skip("events.json not built yet")
    doc = json.loads(EVENTS_JSON.read_text())
    assert doc["methodology_version"] == E.METHODOLOGY_VERSION and doc["declared_at"] == E.DECLARED_AT
    long_lists = [(p, n) for p, n in _numeric_lists(doc) if n > 5]  # windows/CI are 2, q_counts is 5
    assert long_lists == []
    banned = {"price", "prices", "close", "adjclose", "adj_close", "open", "high", "low", "volume", "series", "returns"}
    keys = set(k.lower() for k in _keys({k: v for k, v in doc.items() if k != "per_issuer"}))
    keys |= set(k.lower() for rows in doc["per_issuer"].values() for k in _keys(rows))  # ticker names (e.g. LOW) are not fields
    assert not (keys & banned)
    for rows in doc["per_issuer"].values():
        for r in rows:
            assert set(r) <= {"event_id", "exposure", "car_pp", "car_secondary_pp", "quintile", "sector_baseline", "mention", "filing"}
    assert {e["id"] for e in doc["events"]} <= {e["id"] for e in E.EVENTS}


def test_events_json_ic_recomputes_from_per_issuer():
    if not EVENTS_JSON.exists():
        pytest.skip("events.json not built yet")
    doc = json.loads(EVENTS_JSON.read_text())
    for ev in doc["events"]:
        rows = [r for rs in doc["per_issuer"].values() for r in rs if r["event_id"] == ev["id"]]
        assert len(rows) == ev["n"]
        if ev["n"] >= 3:
            ic = E.spearman([r["exposure"] for r in rows], [r["car_pp"] for r in rows])
            assert abs(ic - ev["ic"]) < 0.01
