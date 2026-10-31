"""Event validation harness (SR 11-7-style outcomes analysis), methodology evt-1.0.

Question: does filing-derived exposure, measured only with information available
before an event, rank-order the event's abnormal returns?

Everything that defines the test is declared in this module *before* any return
was computed (see DECLARED_AT) and is not changed after seeing results:
events and dates, windows, benchmark, exposure variable per channel, naive
baselines and the statistics. Results are reported exactly as measured.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 1. Pre-declaration (written 2026-10-31T03:45Z, before any price was fetched)
# ---------------------------------------------------------------------------

METHODOLOGY_VERSION = "evt-1.0"
DECLARED_AT = "2026-10-31T03:45:00Z"

# Windows in trading days relative to t=0 (the event `date`), inclusive.
WINDOWS = {"primary": (-1, 5), "secondary": (-1, 20), "estimation": (-260, -30)}
MIN_ESTIMATION_OBS = 120
BENCHMARK = "ACWI"           # iShares MSCI ACWI ETF
BENCHMARK_FALLBACK = "SPY"   # used only if ACWI is missing in the needed day range
MAX_FILING_AGE_DAYS = 550    # point-in-time annual filing must be filed < date and within this age
BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 42
SIGNIFICANCE_LEVEL = 0.10    # two-sided, Student t with n-2 df on the IC t statistic

# `date` = first US trading day the market could react. `channel` = scenario channel
# whose exposure is tested (`weaponized` = the 4W weaponized score / 100).
# `mention_regions` = geography counted by the mention-only baseline (raw count of
# the region's mentions in the risk-factor section, no weighting).
# expected_sign "negative": higher exposure -> lower CAR.
EVENTS = [
    {
        "id": "huawei_entity_list_2019",
        "name_ko": "미국 상무부 화웨이 Entity List 지정",
        "name_en": "US adds Huawei and 68 affiliates to the Entity List",
        "date": "2019-05-16",
        "channel": "china_export",
        "mention_regions": ["China"],
        "source_url": "https://www.federalregister.gov/documents/2019/05/21/2019-10616/addition-of-entities-to-the-entity-list",
        "date_note": "BIS announced on Wed 2019-05-15; final rule 84 FR 22961 effective 2019-05-16 (published 2019-05-21). "
                     "t=0 is the effective date; the announcement day is t=-1 and inside the primary window.",
        "expected_sign": "negative",
    },
    {
        "id": "russia_ukraine_2022",
        "name_ko": "러시아의 우크라이나 침공",
        "name_en": "Russia invades Ukraine",
        "date": "2022-02-24",
        "channel": "weaponized",
        "mention_regions": ["Russia", "Ukraine"],
        "source_url": "https://bidenwhitehouse.archives.gov/briefing-room/statements-releases/2022/02/23/statement-by-president-biden-on-russias-unprovoked-and-unjustified-attack-on-ukraine/",
        "date_note": "Invasion began before dawn 2022-02-24 Kyiv time (evening 2022-02-23 US Eastern, after the close); "
                     "first US session 2022-02-24.",
        "expected_sign": "negative",
    },
    {
        "id": "pelosi_taiwan_2022",
        "name_ko": "펠로시 대만 방문과 인민해방군 대만 포위 훈련",
        "name_en": "Pelosi visits Taiwan; PLA announces drills around Taiwan",
        "date": "2022-08-02",
        "channel": "taiwan",
        "mention_regions": ["Taiwan"],
        "source_url": "https://www.cbsnews.com/news/nancy-pelosi-taiwan-china-taipei-arrival-plane/",
        "date_note": "Landed in Taipei Tue 2022-08-02 evening local time (morning US Eastern, during the US session); "
                     "PLA drills announced the same day, live-fire 08-04 to 08-07 (inside the primary window).",
        "expected_sign": "negative",
    },
    {
        "id": "bis_advanced_computing_2022",
        "name_ko": "미국 BIS 첨단 컴퓨팅·반도체 장비 수출통제",
        "name_en": "BIS advanced-computing and semiconductor-manufacturing export controls on China",
        "date": "2022-10-07",
        "channel": "china_export",
        "mention_regions": ["China"],
        "source_url": "https://www.bis.gov/press-release/commerce-implements-new-export-controls-advanced-computing-semiconductor-manufacturing-items-peoples",
        "date_note": "BIS press release dated Fri 2022-10-07; interim final rule 87 FR 62186 effective 2022-10-07. "
                     "Monday 2022-10-10 is t=+1, inside the primary window.",
        "expected_sign": "negative",
    },
    {
        "id": "china_gallium_germanium_2023",
        "name_ko": "중국 갈륨·게르마늄 수출통제 발표",
        "name_en": "China announces gallium and germanium export controls",
        "date": "2023-07-03",
        "channel": "china_export",
        "mention_regions": ["China"],
        "source_url": "http://www.mofcom.gov.cn/article/zwgk/gkzcfb/202307/20230703419666.shtml",
        "date_note": "MOFCOM/GAC Announcement No. 23 of 2023 issued Mon 2023-07-03 (Beijing evening = US morning); "
                     "US session 2023-07-03 was a half day, 2023-07-04 closed.",
        "expected_sign": "negative",
    },
    {
        "id": "red_sea_carrier_pause_2023",
        "name_ko": "홍해: 주요 해운사 바브엘만데브 통항 중단",
        "name_en": "Red Sea: major container carriers pause Bab el-Mandeb transits",
        "date": "2023-12-15",
        "channel": "hormuz",
        "mention_regions": ["Middle East"],
        "source_url": "https://www.thestar.com.my/news/world/2023/12/15/maersk-to-pause-all-container-shipments-through-the-red-sea",
        "date_note": "Maersk and Hapag-Lloyd announced pauses on Fri 2023-12-15 (European day, before/at the US open); "
                     "MSC and CMA CGM followed on 12-16, BP on 12-18 (t=+1). Candidate 2023-12-18 moved to the first "
                     "major-carrier pause. Channel = hormuz (Middle East route channel).",
        "expected_sign": "negative",
    },
    {
        "id": "hormuz_transit_collapse_2026",
        "name_ko": "호르무즈 해협 통항 급감",
        "name_en": "Strait of Hormuz transit collapse",
        "date": "2026-03-04",
        "channel": "hormuz",
        "mention_regions": ["Middle East"],
        "source_url": "https://portwatch.imf.org/pages/chokepoints",
        "crosscheck_url": "https://www.insurancejournal.com/news/international/2026/03/06/860782.htm",
        "date_note": "Rule: first day the IMF PortWatch 7-day mean of chokepoint6 daily transits falls below 50% of its "
                     "365-day baseline (the 365 days before the 7-day window). Measured 2026-03-04: 7-day mean 34.0 vs "
                     "baseline 84.7 (0.40). Daily transits 64 (02-27), 57 (02-28), 20 (03-01), 4 (03-02), 4 (03-03), 0 (03-04). "
                     "Cross-check: JMIC reported a near-total halt with one inbound and one outbound transit on 03-04.",
        "expected_sign": "negative",
    },
]

# Exposure variable per channel (declared with EVENTS, unchanged afterwards):
#   taiwan | china_export | hormuz | malacca -> metrics.channel_exposures(profile, exposure, minerals)[channel]
#   weaponized                               -> exposure.categories["weaponized"]["score"] / 100
# where `exposure` = analyze_exposure(ticker, risk_section(point-in-time annual filing)), own-name
# entities removed exactly as in sentinel.service.exposure, and `minerals` =
# metrics.relevant_minerals(profile, exposure, current USGS table) as in sentinel.service.stress_context.
# `profile` uses only EDGAR submissions metadata (SIC, business-address country); no fundamentals.
#
# Naive baselines (benchmarking principle), same names and CARs as the model exposure:
#   sector  : mean model exposure of the other names in the event cross-section with the same
#             2-digit SIC; a name with no same-SIC2 peer gets the mean of all other names.
#   mention : raw count of `mention_regions` mentions in the risk-factor section (exposure.geographies).
#
# Statistics: log returns of adjusted close; market model OLS on the estimation window (>= 120 obs);
# AR = r_i - (alpha + beta * r_m); CAR = sum of AR over the window (all window days required).
# Spearman rank IC (average ranks for ties); t = IC * sqrt((n-2)/(1-IC^2)); two-sided p from Student t(n-2).
# Quintiles by average rank of exposure: q = ceil(5 * rank / n); Q5-Q1 = mean CAR(Q5) - mean CAR(Q1) in pp,
# Welch t. Bootstrap 95% CI of IC: 2,000 resamples of names with replacement, seed 42, percentile.
# sign_ok = sign(IC) equals the expected sign.

# ---------------------------------------------------------------------------
# 2. Computation (appended after the declaration above; does not modify it)
# ---------------------------------------------------------------------------

import json  # noqa: E402
import math  # noqa: E402
import re  # noqa: E402
import time  # noqa: E402
from datetime import date as _date  # noqa: E402
from typing import Dict, List, Optional, Sequence, Tuple  # noqa: E402

import numpy as np  # noqa: E402

EVENT_BY_ID = {e["id"]: e for e in EVENTS}


class InsufficientData(ValueError):
    pass


# ---- returns and the market model -------------------------------------------

def aligned_log_returns(prices: Sequence[Tuple[str, float]], calendar: Sequence[str]) -> np.ndarray:
    """Log return of each calendar day vs the previous calendar day; NaN where a price is missing."""
    px = dict(prices)
    p = np.array([px.get(d, np.nan) for d in calendar], dtype=float)
    r = np.full(len(calendar), np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        r[1:] = np.log(p[1:] / p[:-1])
    return r


def event_index(calendar: Sequence[str], day: str) -> int:
    """Index of t=0: first calendar trading day on or after `day`."""
    for i, d in enumerate(calendar):
        if d >= day:
            return i
    raise InsufficientData(f"no trading day on/after {day}")


def market_model(r_i: np.ndarray, r_m: np.ndarray, min_obs: int = MIN_ESTIMATION_OBS) -> Tuple[float, float, int]:
    """OLS alpha, beta of r_i on r_m over jointly finite observations."""
    r_i, r_m = np.asarray(r_i, float), np.asarray(r_m, float)
    ok = np.isfinite(r_i) & np.isfinite(r_m)
    n = int(ok.sum())
    if n < min_obs:
        raise InsufficientData(f"{n} estimation observations < {min_obs}")
    x, y = r_m[ok], r_i[ok]
    vx = float(np.var(x))
    if vx <= 0:
        raise InsufficientData("benchmark has zero variance in the estimation window")
    beta = float(np.mean((x - x.mean()) * (y - y.mean())) / vx)
    alpha = float(y.mean() - beta * x.mean())
    return alpha, beta, n


def abnormal_returns(r_i: np.ndarray, r_m: np.ndarray, alpha: float, beta: float) -> np.ndarray:
    return np.asarray(r_i, float) - (alpha + beta * np.asarray(r_m, float))


def car(ar: np.ndarray) -> float:
    """Cumulative abnormal return; NaN if any day in the window is missing."""
    ar = np.asarray(ar, float)
    return float(ar.sum()) if ar.size and np.all(np.isfinite(ar)) else float("nan")


def event_cars(r_i: np.ndarray, r_m: np.ndarray, t0: int, windows: Dict = WINDOWS) -> Dict[str, float]:
    """Market-model CARs (log, fraction) for the declared windows around index t0."""
    e0, e1 = windows["estimation"]
    if t0 + e0 < 1:
        raise InsufficientData("estimation window starts before the price history")
    alpha, beta, n_est = market_model(r_i[t0 + e0 : t0 + e1 + 1], r_m[t0 + e0 : t0 + e1 + 1])
    out = {"alpha": alpha, "beta": beta, "n_est": n_est}
    for name in ("primary", "secondary"):
        a, b = windows[name]
        if t0 + b >= len(r_i):
            out[name] = float("nan")
            continue
        out[name] = car(abnormal_returns(r_i[t0 + a : t0 + b + 1], r_m[t0 + a : t0 + b + 1], alpha, beta))
    return out


# ---- cross-sectional statistics -----------------------------------------------

def rankdata(x: Sequence[float]) -> np.ndarray:
    """1-based ranks, ties get the average rank."""
    x = np.asarray(x, float)
    order = np.argsort(x, kind="mergesort")
    ranks = np.empty(len(x), float)
    xs = x[order]
    i = 0
    while i < len(x):
        j = i
        while j + 1 < len(x) and xs[j + 1] == xs[i]:
            j += 1
        ranks[order[i : j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return ranks


def spearman(x: Sequence[float], y: Sequence[float]) -> float:
    rx, ry = rankdata(x), rankdata(y)
    sx, sy = rx.std(), ry.std()
    if sx == 0 or sy == 0:
        return float("nan")
    return float(np.mean((rx - rx.mean()) * (ry - ry.mean())) / (sx * sy))


def ic_tstat(ic: float, n: int) -> float:
    if not np.isfinite(ic) or n < 3:
        return float("nan")
    if abs(ic) >= 1:
        return float("inf") * np.sign(ic)
    return float(ic * math.sqrt((n - 2) / (1 - ic * ic)))


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the regularized incomplete beta (Numerical Recipes)."""
    tiny, eps = 1e-300, 3e-14
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def betainc_reg(a: float, b: float, x: float) -> float:
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1 - x)
    if x < (a + 1) / (a + b + 2):
        return math.exp(lbeta) * _betacf(a, b, x) / a
    return 1.0 - math.exp(lbeta) * _betacf(b, a, 1 - x) / b


def t_two_sided_p(t: float, df: float) -> float:
    if not np.isfinite(t):
        return 0.0 if np.isinf(t) else float("nan")
    if df <= 0:
        return float("nan")
    return float(betainc_reg(df / 2.0, 0.5, df / (df + t * t)))


def quintiles(x: Sequence[float]) -> np.ndarray:
    """Quintile 1..5 by average rank (ties share a quintile): q = ceil(5 * rank / n)."""
    r = rankdata(x)
    n = len(r)
    return np.clip(np.ceil(5.0 * r / n - 1e-9), 1, 5).astype(int)


def welch_t(a: Sequence[float], b: Sequence[float]) -> float:
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) < 2 or len(b) < 2:
        return float("nan")
    se = math.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
    return float((a.mean() - b.mean()) / se) if se > 0 else float("nan")


def bootstrap_ic_ci(x: Sequence[float], y: Sequence[float], resamples: int = BOOTSTRAP_RESAMPLES, seed: int = BOOTSTRAP_SEED) -> Tuple[float, float]:
    x, y = np.asarray(x, float), np.asarray(y, float)
    rng = np.random.default_rng(seed)
    n = len(x)
    ics = []
    for _ in range(resamples):
        idx = rng.integers(0, n, n)
        v = spearman(x[idx], y[idx])
        if np.isfinite(v):
            ics.append(v)
    if not ics:
        return float("nan"), float("nan")
    lo, hi = np.percentile(ics, [2.5, 97.5])
    return float(lo), float(hi)


def ic_block(x: Sequence[float], y: Sequence[float], expected_sign: str = "negative") -> Dict[str, float]:
    n = len(x)
    ic = spearman(x, y) if n >= 3 else float("nan")
    t = ic_tstat(ic, n)
    p = t_two_sided_p(t, n - 2) if n >= 3 else float("nan")
    want = -1 if expected_sign == "negative" else 1
    return {"n": n, "ic": ic, "ic_t": t, "ic_p": p, "sign_ok": bool(np.isfinite(ic) and np.sign(ic) == want),
            "significant_10pct": bool(np.isfinite(p) and p < SIGNIFICANCE_LEVEL)}


def cross_section(exposure: Sequence[float], car_frac: Sequence[float], expected_sign: str = "negative", bootstrap: bool = True) -> Dict:
    """IC, t, p, bootstrap CI and the Q5-Q1 spread (pp) of CAR on exposure."""
    x = np.asarray(exposure, float)
    y = np.asarray(car_frac, float) * 100.0
    out = ic_block(x, y, expected_sign)
    q = quintiles(x) if len(x) else np.array([], int)
    q5, q1 = y[q == 5], y[q == 1]
    out["q5q1_pp"] = float(q5.mean() - q1.mean()) if len(q5) and len(q1) else float("nan")
    out["q5q1_t"] = welch_t(q5, q1)
    out["q_counts"] = [int((q == k).sum()) for k in range(1, 6)]
    out["quintile"] = q.tolist()
    # Supplementary (added 2026-10-31 after the 40-name run, not part of the declaration): when ties leave Q1 or Q5
    # empty under the declared rule, Q5-Q1 stays NaN and this reports top minus bottom *non-empty* quintile instead.
    nonempty = [k for k in range(1, 6) if (q == k).any()]
    if nonempty:
        lo, hi = nonempty[0], nonempty[-1]
        out["q_extreme"] = [lo, hi]
        out["q_extreme_spread_pp"] = float(y[q == hi].mean() - y[q == lo].mean()) if hi != lo else float("nan")
        out["q_extreme_t"] = welch_t(y[q == hi], y[q == lo]) if hi != lo else float("nan")
    if bootstrap and len(x) >= 3:
        out["ic_ci"] = list(bootstrap_ic_ci(x, y))
    return out


def sector_baseline(exposure: Sequence[float], sic2: Sequence[str]) -> np.ndarray:
    """Leave-one-out mean exposure of same-SIC2 names; names without a peer get the leave-one-out mean of all."""
    x = np.asarray(exposure, float)
    n = len(x)
    out = np.empty(n)
    total = x.sum()
    for i in range(n):
        peers = [j for j in range(n) if j != i and sic2[j] and sic2[j] == sic2[i]]
        out[i] = float(np.mean(x[peers])) if peers else (total - x[i]) / max(1, n - 1)
    return out


def summarize(events: List[Dict]) -> Dict:
    ok = [e for e in events if e.get("n", 0) >= 3 and e.get("ic") is not None and np.isfinite(e["ic"])]

    def mean(vals):
        vals = [v for v in vals if v is not None and np.isfinite(v)]
        return round(float(np.mean(vals)), 4) if vals else None

    s = {
        "events_n": len(ok),
        "sign_ok_n": sum(1 for e in ok if e["sign_ok"]),
        "significant_10pct_n": sum(1 for e in ok if e["significant_10pct"]),
        "significant_10pct_expected_sign_n": sum(1 for e in ok if e["significant_10pct"] and e["sign_ok"]),
        "mean_ic": mean([e["ic"] for e in ok]),
        "mean_ic_secondary": mean([e.get("ic_secondary") for e in ok]),
        "mean_q5q1_pp": mean([e.get("q5q1_pp") for e in ok]),
    }
    for b in ("sector", "mention"):
        det = [e["baselines_detail"][b] for e in ok if b in e.get("baselines_detail", {})]
        s[f"baseline_{b}_mean_ic"] = mean([d["ic"] for d in det])
        s[f"baseline_{b}_sign_ok_n"] = sum(1 for d in det if d["sign_ok"])
        s[f"baseline_{b}_significant_10pct_n"] = sum(1 for d in det if d["significant_10pct"])
    return s


# ---- PortWatch rule for the Hormuz event date --------------------------------

def portwatch_threshold_date(series: Sequence[Tuple[str, float]], ratio: float = 0.5, after: str = "") -> Optional[Dict]:
    """First day whose trailing 7-day mean < ratio x mean of the 365 days before that 7-day window."""
    s = sorted(series)
    n = np.array([v for _, v in s], float)
    for i in range(371, len(s)):
        if s[i][0] < after:
            continue
        m7 = n[i - 6 : i + 1].mean()
        base = n[i - 371 : i - 6].mean()
        if base > 0 and m7 < ratio * base:
            return {"date": s[i][0], "mean_7d": round(float(m7), 1), "baseline_365d": round(float(base), 1), "ratio": round(float(m7 / base), 3)}
    return None


def hormuz_rule_date(after: str = "2026-01-01") -> Optional[Dict]:
    """Recompute event 7's date from IMF PortWatch (network)."""
    from sentinel.http import fetch_json
    from sentinel.sources.portwatch import CHOKEPOINT_DAILY_URL

    rows, off = [], 0
    while True:
        p = {"where": "portid='chokepoint6' AND date >= DATE '2024-01-01'", "outFields": "date,portid,n_total",
             "orderByFields": "date ASC", "resultOffset": off, "resultRecordCount": 2000, "returnGeometry": "false", "f": "json"}
        d, _ = fetch_json(CHOKEPOINT_DAILY_URL, params=p, ttl=7 * 86400)
        feats = d.get("features", [])
        if not feats:
            break
        rows += [f["attributes"] for f in feats]
        off += len(feats)
        if not d.get("exceededTransferLimit"):
            break
    series = [(str(r["date"])[:10], float(r["n_total"])) for r in rows if r.get("n_total") is not None]
    return portwatch_threshold_date(series, after=after)


# ---- point-in-time exposure (network + disk cache) ---------------------------

SEC_MIN_INTERVAL_S = 0.25  # <= 4 requests/second (two workers share SEC's 10 req/s)
_sec_last = [0.0]


def _sec_get(url: str, ttl: int) -> Tuple[bytes, str]:
    """SEC GET through sentinel.http with pacing applied only to live (uncached) requests."""
    from sentinel.config import SEC_USER_AGENT  # noqa: F401  (sentinel.http sends it)
    from sentinel.http import _cache_path, fetch_bytes

    path = _cache_path(url, None)
    meta = path.with_suffix(".meta")
    fresh = False
    try:
        fresh = path.exists() and time.time() - json.loads(meta.read_text())["ts"] < ttl
    except Exception:
        fresh = False
    if not fresh:
        wait = SEC_MIN_INTERVAL_S - (time.time() - _sec_last[0])
        if wait > 0:
            time.sleep(wait)
        _sec_last[0] = time.time()
    return fetch_bytes(url, ttl=ttl)


def sec_json(url: str, params=None, ttl: int = 30 * 86400, headers=None):
    body, retrieved = _sec_get(url, ttl)
    return json.loads(body), retrieved


def _exposure_cache_dir():
    from sentinel.config import CACHE_DIR

    d = CACHE_DIR / "eventstudy" / "exposure"
    d.mkdir(parents=True, exist_ok=True)
    return d


def exposure_cache_path(cik: str, accession: str):
    return _exposure_cache_dir() / f"{cik}_{accession}.json"


def build_exposure(ticker: str, cik: str, company_name: str, filing_dict: Dict) -> Dict:
    """Parse one point-in-time filing into an Exposure dict, cached per (cik, accession). Runs in worker processes."""
    from sentinel.analytics.exposure import analyze_exposure
    from sentinel.models import FilingRef
    from sentinel.sources.filings import risk_section

    path = exposure_cache_path(cik, filing_dict["accession"])
    if path.exists():
        return json.loads(path.read_text())
    filing = FilingRef(**filing_dict)
    e = analyze_exposure(ticker, risk_section(filing))
    own = {w.lower() for w in re.findall(r"[A-Za-z]{3,}", company_name or "")}
    e.entities_named = [x for x in e.entities_named if not ({w.lower() for w in re.findall(r"[A-Za-z]{3,}", x)} & own)]
    d = e.model_dump()
    for c in d.get("categories", {}).values():  # quotes are not needed for the statistics
        c["top"] = []
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(d))
    tmp.replace(path)
    return d


def exposure_values(profile, exposure, all_minerals, channel: str, mention_regions: Sequence[str]) -> Dict[str, float]:
    """Model exposure for `channel` plus the raw mention count for the mention-only baseline."""
    from sentinel.analytics import metrics as M

    if channel == "weaponized":
        x = float(exposure.categories.get("weaponized", {}).get("score", 0.0)) / 100.0
    else:
        rel = M.relevant_minerals(profile, exposure, all_minerals)
        x = float(M.channel_exposures(profile, exposure, [m for m, _ in rel])[channel])
    mentions = sum(g.mentions for g in exposure.geographies if g.region in set(mention_regions))
    return {"exposure": x, "mention": float(mentions)}


def filing_label(f: Dict) -> str:
    return f"{f['form']} {f['filed']}"


def days_between(a: str, b: str) -> int:
    return (_date.fromisoformat(b) - _date.fromisoformat(a)).days
