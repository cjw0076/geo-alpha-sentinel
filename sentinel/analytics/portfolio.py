"""Portfolio lens: a holdings book vs a benchmark book, channel by channel.

* Issuer vectors come from the single-issuer pipeline (`service.stress_context`,
  `service.stress_and_metrics`, policy `_recommendation`), so every issuer number in a
  portfolio view is the same number the issuer drill-down shows.
* Coverage: the book's top positions by weight until cumulative weight >= 90% or 300 names;
  the benchmark's top 500 SEC annual-report filers by weight. All aggregates are computed on
  the covered names renormalised to 1 (`covered_weight` reports the share they represent).
* Channel exposure `E_k = sum_i w_i e_ik`; active `100 (E_k - B_k)` in percentage points;
  concentration of contributions `w_i e_ik` as HHI and effective N = 1 / HHI.
* Joint scenario: `PORTFOLIO_STRESS_PROGRAM` (sentinel/analytics/stress.py), sandbox-executed.
"""

from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from sentinel.analytics.stress import INPUT_COST_BETA, MAX_REVENUE_LOSS
from sentinel.config import MC_PATHS, MC_SEED
from sentinel.models import HoldingsBook, Scenario

CHANNELS: Tuple[str, ...] = ("taiwan", "china_export", "hormuz", "malacca")
CHANNEL_META: Dict[str, Dict[str, Optional[str]]] = {
    "taiwan": {"label_ko": "대만 해협", "label_en": "Taiwan Strait", "chokepoint_id": "chokepoint11"},
    "china_export": {"label_ko": "중국 핵심광물 수출통제", "label_en": "China critical-mineral export controls", "chokepoint_id": None},
    "hormuz": {"label_ko": "호르무즈 해협", "label_en": "Strait of Hormuz", "chokepoint_id": "chokepoint6"},
    "malacca": {"label_ko": "말라카 해협", "label_en": "Malacca Strait", "chokepoint_id": "chokepoint5"},
}
DEFAULT_SCENARIO = Scenario(taiwan=0.5, china_export=0.5, hormuz=0.2, malacca=0.2, horizon=2027)
BOOK_TARGET_WEIGHT = 0.90
BOOK_MAX_NAMES = 300
BENCH_MAX_FILERS = 500
ISSUER_SHOCK_FLOOR = -1.0      # per-issuer FCF shock floor inside portfolio sums (-100% of base FCF)
MAX_FILING_AGE_DAYS = 550      # an annual report older than this (vs the book date) is not used
MAX_BASE_YEAR_LAG = 2          # latest XBRL revenue year must be >= book year - 2
MATERIAL_EXPOSURE = 0.5        # a holding is 'materially exposed' to a channel when its exposure e_ik >= this


# ---------------------------------------------------------------------------
# Positions and coverage
# ---------------------------------------------------------------------------

def positions(book: HoldingsBook) -> List[Dict[str, Any]]:
    """Issuer-level positions: rows aggregated by CIK (share classes merged); unmapped rows stay
    one position per identifier. Ticker/name come from the largest row. Sorted by weight."""
    agg: Dict[str, Dict[str, Any]] = {}
    for r in book.rows:
        key = f"cik:{r.cik}" if r.cik else f"id:{r.cusip or r.isin or r.name}"
        p = agg.get(key)
        if p is None:
            agg[key] = {"key": key, "ticker": r.ticker, "cik": r.cik, "name": r.name, "weight": r.weight, "value_usd": r.value_usd,
                        "sec_filer": r.sec_filer, "map_method": r.map_method, "ids": [r.cusip or r.isin], "_top": r.weight}
        else:
            p["weight"] += r.weight
            p["value_usd"] += r.value_usd
            p["ids"].append(r.cusip or r.isin)
            p["sec_filer"] = p["sec_filer"] or r.sec_filer
            if r.weight > p["_top"]:
                p.update(ticker=r.ticker, name=r.name, map_method=r.map_method, _top=r.weight)
    out = sorted(agg.values(), key=lambda p: -p["weight"])
    for p in out:
        p.pop("_top", None)
    return out


def equal_weight_positions(tickers: Sequence[str], names: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    w = 1.0 / len(tickers)
    return [{"key": f"t:{t}", "ticker": t, "cik": None, "name": (names or {}).get(t, t), "weight": w, "value_usd": None, "sec_filer": True,
             "map_method": "universe", "ids": []} for t in tickers]


def select_book(pos: List[Dict[str, Any]], target: float = BOOK_TARGET_WEIGHT, max_names: int = BOOK_MAX_NAMES) -> List[Dict[str, Any]]:
    """Top positions by weight until cumulative weight >= target or max_names (whichever first)."""
    out, cum = [], 0.0
    for p in sorted(pos, key=lambda p: -p["weight"]):
        if cum >= target - 1e-12 or len(out) >= max_names:
            break
        out.append(p)
        cum += p["weight"]
    return out


def select_bench(pos: List[Dict[str, Any]], max_filers: int = BENCH_MAX_FILERS) -> List[Dict[str, Any]]:
    """Top `max_filers` SEC annual-report filers by weight."""
    return [p for p in sorted(pos, key=lambda p: -p["weight"]) if p.get("sec_filer") and p.get("ticker")][:max_filers]


# ---------------------------------------------------------------------------
# Issuer vectors
# ---------------------------------------------------------------------------

def issuer_vector(ticker: str, scenario: Scenario = DEFAULT_SCENARIO) -> Dict[str, Any]:
    """Channel exposures, fundamentals for the stress program, metrics and policy verdict of one issuer."""
    from sentinel import service
    from sentinel.agent.runner import _recommendation

    c = service.stress_context(ticker)
    st, mets, chans = service.stress_and_metrics(ticker, scenario)
    md = {m.code: m for m in mets}
    p, e = c["profile"], c["exposure"]
    f = p.latest_annual
    return {
        "ticker": p.ticker, "cik": p.cik, "name": p.name,
        "chans": {k: round(float(chans.get(k, 0.0)), 4) for k in CHANNELS},
        "rev": float(c["rev"]), "gm": float(c["gm"]), "fcf": float(c["fcf"]), "base_year": int(c["base_year"]),
        "metrics": {k: m.value for k, m in md.items()}, "far_rating": md["FAR"].rating if "FAR" in md else None,
        "verdict": _recommendation(md),
        "fcf_p05": st.fcf_shock_pct.get("p05"), "fcf_p50": st.fcf_shock_pct.get("p50"), "rev_p05": st.revenue_shock_pct.get("p05"),
        "p_dd": st.prob_fcf_drawdown_gt_20,
        "filing": {"form": f.form, "filed": f.filed, "url": f.url, "accession": f.accession} if f else None,
        "section": e.section_title, "retrieved_at": p.provenance[-1].retrieved_at if p.provenance else None,
    }


def issuer_vectors(tickers: Iterable[str], max_workers: int = 4, scenario: Scenario = DEFAULT_SCENARIO,
                   log: Optional[Callable[[str], None]] = None) -> Dict[str, Dict[str, Any]]:
    """ticker -> vector; failures are recorded as {'error': reason} instead of raising."""
    tickers = list(dict.fromkeys(t for t in tickers if t))
    out: Dict[str, Dict[str, Any]] = {}

    def one(t: str) -> Tuple[str, Dict[str, Any]]:
        try:
            return t, issuer_vector(t, scenario)
        except Exception as exc:  # recorded, not raised
            return t, {"error": f"{type(exc).__name__}: {exc}"[:240]}

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for i, (t, v) in enumerate(ex.map(one, tickers), 1):
            out[t] = v
            if log and (i % 25 == 0 or i == len(tickers)):
                log(f"  issuer vectors {i}/{len(tickers)} ({sum(1 for x in out.values() if 'error' in x)} failures)")
    return out


def quality_issue(v: Dict[str, Any], as_of: Optional[str] = None) -> Optional[str]:
    """Input checks on an issuer vector before it enters a portfolio aggregate (None = usable)."""
    from datetime import date

    if float(v.get("rev") or 0.0) <= 1.0:
        return "no USD revenue in XBRL company facts"
    if not -1.0 <= float(v.get("gm", 0.5)) <= 1.0:
        return f"gross margin {float(v['gm']):.3g} outside [-1, 1] (revenue / gross-profit concepts disagree)"
    if as_of:
        y = int(as_of[:4])
        if int(v.get("base_year") or 0) < y - MAX_BASE_YEAR_LAG:
            return f"latest XBRL revenue year FY{v.get('base_year')} is older than FY{y - MAX_BASE_YEAR_LAG}"
        f = v.get("filing") or {}
        if f.get("filed") and (date.fromisoformat(as_of) - date.fromisoformat(f["filed"])).days > MAX_FILING_AGE_DAYS:
            return f"latest annual report {f.get('form')} filed {f['filed']} is more than {MAX_FILING_AGE_DAYS} days before {as_of}"
    return None


def attach_vectors(pos: List[Dict[str, Any]], vectors: Dict[str, Dict[str, Any]], as_of: Optional[str] = None) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Split selected positions into (covered, failures) using the vectors dict and `quality_issue`."""
    covered, failed = [], []
    for p in pos:
        v = vectors.get(p["ticker"]) if p.get("ticker") else None
        if not p.get("ticker"):
            failed.append({**p, "reason": "no SEC ticker mapping (OpenFIGI / SEC name)"})
        elif not p.get("sec_filer"):
            failed.append({**p, "reason": f"no 10-K / 20-F / 40-F on EDGAR within {MAX_FILING_AGE_DAYS} days"})
        elif v is None or "error" in v:
            failed.append({**p, "reason": (v or {}).get("error", "not analysed")})
        elif quality_issue(v, as_of):
            failed.append({**p, "reason": quality_issue(v, as_of)})
        else:
            covered.append({**p, "vec": v, "cik": p.get("cik") or v["cik"]})
    return covered, failed


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def renormalise(covered: List[Dict[str, Any]]) -> Dict[str, float]:
    """issuer id -> weight renormalised over the covered names (sums to 1)."""
    tot = sum(p["weight"] for p in covered)
    return {issuer_id(p): p["weight"] / tot for p in covered} if tot > 0 else {}


def issuer_id(p: Dict[str, Any]) -> str:
    return p.get("cik") or (p.get("vec") or {}).get("cik") or p["ticker"]


def channel_table(book: List[Dict[str, Any]], bench: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Per channel: E_k, B_k, active_pp, contributions, HHI, effective N, active contributions (union)."""
    wb, wm = renormalise(book), renormalise(bench)
    vec = {issuer_id(p): p for p in list(bench) + list(book)}
    out: Dict[str, Dict[str, Any]] = {}
    for k in CHANNELS:
        contrib = {i: wb[i] * vec[i]["vec"]["chans"][k] for i in wb}
        e_book = sum(contrib.values())
        e_bench = sum(wm[i] * vec[i]["vec"]["chans"][k] for i in wm)
        hhi = sum((c / e_book) ** 2 for c in contrib.values()) if e_book > 0 else 0.0
        union = set(wb) | set(wm)
        active = {i: (wb.get(i, 0.0) - wm.get(i, 0.0)) * vec[i]["vec"]["chans"][k] for i in union}
        m_book = sum(wb[i] for i in wb if vec[i]["vec"]["chans"][k] >= MATERIAL_EXPOSURE)
        m_bench = sum(wm[i] for i in wm if vec[i]["vec"]["chans"][k] >= MATERIAL_EXPOSURE)
        out[k] = {"book": e_book, "bench": e_bench, "active_pp": 100.0 * (e_book - e_bench), "contrib": contrib, "hhi": hhi,
                  "effective_n": (1.0 / hhi) if hhi > 0 else 0.0, "active": active,
                  "material_book": m_book, "material_bench": m_bench, "material_active_pp": 100.0 * (m_book - m_bench)}
    return out


# ---------------------------------------------------------------------------
# Joint scenario Monte Carlo
# ---------------------------------------------------------------------------

def joint_stress(issuers: List[Dict[str, Any]], w_book: Sequence[float], w_bench: Sequence[float], scenario: Scenario = DEFAULT_SCENARIO,
                 paths: int = MC_PATHS, seed: int = MC_SEED, chunk: int = 64, timeout_s: float = 600.0,
                 issuer_floor: float = ISSUER_SHOCK_FLOOR) -> Dict[str, Any]:
    """Run PORTFOLIO_STRESS_PROGRAM in the sandbox. `issuers` are vectors (chans, rev, gm, fcf, base_year)."""
    from sentinel import sandbox
    from sentinel.analytics.stress import PORTFOLIO_STRESS_PROGRAM

    rev = [float(v["rev"]) for v in issuers]
    fcf = [float(v["fcf"]) if float(v["fcf"]) != 0 else 0.01 * float(v["rev"]) for v in issuers]  # run_stress rule
    inputs: Dict[str, Any] = {
        "seed": seed, "paths": paths, "horizon": scenario.horizon, "chunk": chunk, "issuer_floor": float(issuer_floor),
        "severity": {"taiwan": scenario.taiwan, "china_export": scenario.china_export, "hormuz": scenario.hormuz, "malacca": scenario.malacca},
        "exposure": [[round(float(v["chans"][k]), 4) for k in CHANNELS] for v in issuers],
        "base_year": [int(v["base_year"]) for v in issuers],
        "base_revenue": rev,
        "base_cogs": [float(v["rev"]) * (1 - float(v["gm"])) for v in issuers],
        "gross_margin": [float(v["gm"]) for v in issuers],
        "base_fcf": fcf,
        "w_book": [float(x) for x in w_book], "w_bench": [float(x) for x in w_bench],
        "max_revenue_loss": MAX_REVENUE_LOSS, "input_cost_beta": INPUT_COST_BETA,
    }
    out = sandbox.run(PORTFOLIO_STRESS_PROGRAM, inputs, timeout_s=timeout_s)
    r = out["result"]
    r["sandbox_ms"] = out["ms"]
    r["paths"], r["seed"] = paths, seed
    return r


def top_channel(vec: Dict[str, Any], scenario: Scenario = DEFAULT_SCENARIO) -> str:
    """Channel with the largest expected revenue-loss intensity e_k · L_k · (0.5 + 0.5 s_k) · p_k for this issuer."""
    years = max(1, scenario.horizon - int(vec.get("base_year", scenario.horizon - 1)))
    sev = {"taiwan": scenario.taiwan, "china_export": scenario.china_export, "hormuz": scenario.hormuz, "malacca": scenario.malacca}

    def score(k: str) -> float:
        p = min(0.95, sev[k] * (0.55 + 0.10 * years))
        return vec["chans"][k] * MAX_REVENUE_LOSS[k] * (0.5 + 0.5 * sev[k]) * p

    return max(CHANNELS, key=score)


def rank_actions(book: List[Dict[str, Any]], bench: List[Dict[str, Any]], tail_contrib_pp: Dict[str, float], n: int = 5) -> List[Dict[str, Any]]:
    """Rank covered book names by (1) contribution to the book's tail (ES95 paths, most negative first) and
    (2) active exposure sum_k (w_i - b_i) e_ik (largest first); take the n best by rank sum
    (ties: tail contribution)."""
    wb, wm = renormalise(book), renormalise(bench)
    items = []
    for p in book:
        i = issuer_id(p)
        act = sum((wb[i] - wm.get(i, 0.0)) * p["vec"]["chans"][k] for k in CHANNELS)
        items.append({"p": p, "id": i, "tail": tail_contrib_pp.get(i, 0.0), "active_exposure": act})
    by_tail = sorted(items, key=lambda x: x["tail"])
    by_act = sorted(items, key=lambda x: -x["active_exposure"])
    r1 = {x["id"]: j for j, x in enumerate(by_tail)}
    r2 = {x["id"]: j for j, x in enumerate(by_act)}
    for x in items:
        x["rank_tail"], x["rank_active"] = r1[x["id"]] + 1, r2[x["id"]] + 1
        x["score"] = x["rank_tail"] + x["rank_active"]
    items.sort(key=lambda x: (x["score"], x["tail"]))
    return items[:n]


def headline_chokepoint(chokepoints: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Same rule as public/js/app.js anomaly(), restricted to the channel chokepoints: largest absolute
    shortfall (baseline − 7-day mean) among Δ ≤ −25% with baseline ≥ 10 ships/day; else the most negative Δ."""
    ids = {m["chokepoint_id"] for m in CHANNEL_META.values() if m["chokepoint_id"]}
    items = [c for c in chokepoints if c.get("id") in ids and c.get("baseline_365d") is not None and c.get("delta_pct") is not None
             and c.get("mean_7d") is not None and float(c["baseline_365d"]) >= 10]
    hit = sorted([c for c in items if float(c["delta_pct"]) <= -25], key=lambda c: -(float(c["baseline_365d"]) - float(c["mean_7d"])))
    if hit:
        return hit[0]
    items.sort(key=lambda c: float(c["delta_pct"]))
    return items[0] if items else None


def hhi_effective_n(values: Iterable[float]) -> Tuple[float, float]:
    vals = [v for v in values if v > 0]
    tot = sum(vals)
    if tot <= 0:
        return 0.0, 0.0
    hhi = sum((v / tot) ** 2 for v in vals)
    return hhi, 1.0 / hhi


def fmt_signed(x: float, nd: int = 1) -> str:
    return f"{x:+.{nd}f}"


def pct(x: float, nd: int = 2) -> float:
    return round(100.0 * x, nd) if x is not None and math.isfinite(x) else 0.0


# ---------------------------------------------------------------------------
# View assembly (public/snapshots/portfolio/*.json)
# ---------------------------------------------------------------------------

METHODOLOGY_VERSION = "portfolio-1.0"
VERDICT_KO = {"REDUCE": "노출 축소 검토", "HEDGE": "헤지 검토", "HOLD": "모니터링", "ACCUMULATE": "여력 있음"}
REPO_URL = "https://github.com/cjw0076/geo-alpha-sentinel/blob/main"


def _prov_model(locator: str, now: str):
    from sentinel.models import Provenance

    return Provenance(source="model", title=f"Geo-Alpha Sentinel portfolio lens — {locator}", url=f"{REPO_URL}/docs/METHODOLOGY.md#8-portfolio-lens",
                      retrieved_at=now, license="Apache-2.0 (computed from the cited evidence)", locator=locator)


def build_view(book_meta: Dict[str, Any], bench_meta: Dict[str, Any], book_sel: List[Dict[str, Any]], bench_sel: List[Dict[str, Any]],
               bench_all: List[Dict[str, Any]], vectors: Dict[str, Dict[str, Any]], chokepoints: List[Dict[str, Any]],
               scenario: Scenario = DEFAULT_SCENARIO, paths: int = MC_PATHS, seed: int = MC_SEED, n_actions: int = 5) -> Dict[str, Any]:
    """Assemble the portfolio JSON (shape documented in collab brief eng-01 §3).

    book_meta:  id, name_ko, name_en, as_of, filed, holdings_n, value_usd, subject_ko, subject_en,
                source (Provenance of the book), weight_basis ('13f' | 'equal')
    bench_meta: name_ko, name_en, as_of, source (Provenance)
    """
    from sentinel.agent import verifier
    from sentinel.http import utcnow_iso
    from sentinel.ledger import Ledger
    from sentinel.models import Provenance

    now = utcnow_iso()
    L = Ledger()
    covered_b, failed_b = attach_vectors(book_sel, vectors, book_meta.get("as_of"))
    covered_m, failed_m = attach_vectors(bench_sel, vectors, bench_meta.get("as_of"))
    if not covered_b or not covered_m:
        raise ValueError("no covered names in book or benchmark")
    cov_w_b = sum(p["weight"] for p in covered_b)
    cov_w_m = sum(p["weight"] for p in covered_m)
    wb, wm = renormalise(covered_b), renormalise(covered_m)
    bench_raw = {}
    for p in bench_all:
        if p.get("cik"):
            bench_raw[p["cik"]] = bench_raw.get(p["cik"], 0.0) + p["weight"]

    e_book = L.add("table", f"{book_meta['name_en']}: holdings ({book_meta['holdings_n']} rows), as of {book_meta['as_of']}", book_meta["source"],
                   value=book_meta.get("value_usd"), unit="USD" if book_meta.get("value_usd") else None,
                   data={"covered_n": len(covered_b), "covered_weight": round(cov_w_b, 6)}, key="book")
    e_bench = L.add("table", f"{bench_meta['name_en']}: equity holdings, as of {bench_meta['as_of']}", bench_meta["source"],
                    data={"covered_n": len(covered_m), "covered_weight": round(cov_w_m, 6)}, key="bench")

    def exposure_ev(p: Dict[str, Any], k: str) -> str:
        v = p["vec"]
        f = v.get("filing") or {}
        prov = Provenance(source="model", title=f"{v['name']} {f.get('form', '')} filed {f.get('filed', '')} — scenario channel exposure",
                          url=f.get("url") or f"{REPO_URL}/docs/METHODOLOGY.md", retrieved_at=v.get("retrieved_at") or now,
                          license="Apache-2.0 (computed from the cited SEC filing, public domain)", locator=f"METHODOLOGY §3 channel exposure: {k}")
        return L.add("number", f"{v['ticker']} channel exposure: {CHANNEL_META[k]['label_en']} (0-1)", prov, value=round(v["chans"][k], 2), unit="0-1",
                     data={"exact": v["chans"][k]}, key=f"exp:{v['ticker']}:{k}")

    def weight_ev(p: Dict[str, Any]) -> str:
        if book_meta.get("weight_basis") == "equal":
            return L.add("number", f"{p['ticker']} weight in {book_meta['name_en']} (equal weight)", _prov_model("equal-weight coverage book", now),
                         value=round(100 * p["weight"], 2), unit="%", key=f"w:{p['ticker']}")
        src = book_meta["source"]
        prov = Provenance(source=src.source, title=src.title, url=src.url, retrieved_at=src.retrieved_at, license=src.license,
                          locator=f"{', '.join(i for i in p.get('ids', []) if i)}: value / book total")
        return L.add("number", f"{p['ticker']} weight in {book_meta['name_en']}", prov, value=round(100 * p["weight"], 2), unit="%",
                     data={"value_usd": p.get("value_usd"), "exact": p["weight"]}, key=f"w:{p['ticker']}")

    def bench_weight_ev(p: Dict[str, Any], w: float) -> str:
        src = bench_meta["source"]
        prov = Provenance(source=src.source, title=src.title, url=src.url, retrieved_at=src.retrieved_at, license=src.license, locator="valUSD / equity total (Korea removed)")
        return L.add("number", f"{p['ticker']} weight in {bench_meta['name_en']}", prov, value=round(100 * w, 2), unit="%", data={"exact": w}, key=f"bw:{p['ticker']}")

    # ---- channels ---------------------------------------------------------
    tab = channel_table(covered_b, covered_m)
    by_id_b = {issuer_id(p): p for p in covered_b}
    by_id_all = {issuer_id(p): p for p in list(covered_m) + list(covered_b)}
    chk_by_id = {c.get("id"): c for c in chokepoints}
    chk_ev: Dict[str, str] = {}
    for k in CHANNELS:
        cid = CHANNEL_META[k]["chokepoint_id"]
        c = chk_by_id.get(cid) if cid else None
        if c and c.get("delta_pct") is not None and c.get("provenance"):
            chk_ev[k] = L.add("number", f"{c['name']}: 7-day mean transits vs 365-day baseline (%)", Provenance(**c["provenance"]), value=round(float(c["delta_pct"]), 1), unit="%",
                              data={"mean_7d": c.get("mean_7d"), "baseline_365d": c.get("baseline_365d"), "as_of": (c.get("latest") or {}).get("date")}, key=f"chk:{cid}")
    channels_out = []
    ch_ev: Dict[str, Dict[str, str]] = {}
    for k in CHANNELS:
        t = tab[k]
        meta = CHANNEL_META[k]
        ids = {
            "book": L.add("number", f"{book_meta['name_en']}: exposure-weighted share, {meta['label_en']} channel (covered names renormalised)", _prov_model("channel exposure E_k = Σ w_i e_ik", now),
                          value=round(100 * t["book"], 1), unit="%", data={"exact_pct": 100 * t["book"]}, key=f"ch:book:{k}"),
            "bench": L.add("number", f"{bench_meta['name_en']}: exposure-weighted share, {meta['label_en']} channel (covered names renormalised)", _prov_model("channel exposure B_k = Σ b_i e_ik", now),
                           value=round(100 * t["bench"], 1), unit="%", data={"exact_pct": 100 * t["bench"]}, key=f"ch:bench:{k}"),
            "active": L.add("number", f"Active exposure vs benchmark, {meta['label_en']} channel (percentage points)", _prov_model("active exposure 100·(E_k − B_k)", now),
                            value=round(t["active_pp"], 1), unit="pp", data={"exact_pp": t["active_pp"]}, key=f"ch:active:{k}"),
        }
        ids["material_book"] = L.add("number", f"{book_meta['name_en']}: weight share of covered holdings with {meta['label_en']} exposure >= {MATERIAL_EXPOSURE} (covered names renormalised)",
                                     _prov_model("material exposure share Σ w_i·1[e_ik ≥ 0.5]", now), value=round(100 * t["material_book"], 1), unit="%",
                                     data={"exact_pct": 100 * t["material_book"]}, key=f"ch:mbook:{k}")
        ids["material_bench"] = L.add("number", f"{bench_meta['name_en']}: weight share of covered holdings with {meta['label_en']} exposure >= {MATERIAL_EXPOSURE} (covered names renormalised)",
                                      _prov_model("material exposure share Σ b_i·1[e_ik ≥ 0.5]", now), value=round(100 * t["material_bench"], 1), unit="%",
                                      data={"exact_pct": 100 * t["material_bench"]}, key=f"ch:mbench:{k}")
        ids["material_active"] = L.add("number", f"Material-exposure share vs benchmark, {meta['label_en']} channel (percentage points)", _prov_model("material exposure active share", now),
                                       value=round(t["material_active_pp"], 1), unit="pp", data={"exact_pp": t["material_active_pp"]}, key=f"ch:mactive:{k}")
        ch_ev[k] = ids
        top = []
        for i, c in sorted(t["contrib"].items(), key=lambda kv: -kv[1])[:5]:
            p = by_id_b[i]
            top.append({"ticker": p["vec"]["ticker"], "name": p["vec"]["name"], "weight_pct": round(100 * p["weight"], 3), "exposure": round(p["vec"]["chans"][k], 4),
                        "contribution_pp": round(100 * c, 3), "evidence_id": exposure_ev(p, k)})
        top_active = []
        for i, a in sorted(t["active"].items(), key=lambda kv: -abs(kv[1]))[:5]:
            p = by_id_all[i]
            top_active.append({"ticker": p["vec"]["ticker"], "name": p["vec"]["name"], "weight_pct": round(100 * by_id_b[i]["weight"], 3) if i in by_id_b else 0.0,
                               "bench_weight_pct": round(100 * bench_raw.get(p["vec"]["cik"], 0.0), 3), "exposure": round(p["vec"]["chans"][k], 4),
                               "active_pp": round(100 * a, 3), "evidence_id": exposure_ev(p, k)})
        channels_out.append({
            "id": k, "label_ko": meta["label_ko"], "label_en": meta["label_en"],
            "book_pct": round(100 * t["book"], 2), "bench_pct": round(100 * t["bench"], 2), "active_pp": round(t["active_pp"], 2),
            "book_material_pct": round(100 * t["material_book"], 2), "bench_material_pct": round(100 * t["material_bench"], 2),
            "material_active_pp": round(t["material_active_pp"], 2), "material_threshold": MATERIAL_EXPOSURE,
            "effective_n": round(t["effective_n"], 1), "hhi": round(t["hhi"], 4), "chokepoint_id": meta["chokepoint_id"],
            "top": top, "top_active": top_active,
            "evidence_ids": [ids["book"], ids["bench"], ids["active"], ids["material_book"], ids["material_bench"], ids["material_active"]] + ([chk_ev[k]] if k in chk_ev else []),
        })

    # ---- joint stress -----------------------------------------------------
    order = [issuer_id(p) for p in covered_b] + [i for i in wm if i not in wb]
    vec_order = [by_id_all[i]["vec"] for i in order]
    st = joint_stress(vec_order, [wb.get(i, 0.0) for i in order], [wm.get(i, 0.0) for i in order], scenario, paths=paths, seed=seed)
    tail = {i: st["tail_contrib_pp"][j] for j, i in enumerate(order)}
    iss_p05 = {i: st["issuer_p05_pct"][j] for j, i in enumerate(order)}
    sb_prov = Provenance(source="sandbox", title=f"Sandbox joint Monte Carlo ({paths:,} paths, seed {seed})", url=f"{REPO_URL}/sentinel/analytics/stress.py",
                         retrieved_at=now, license="Apache-2.0", locator="portfolio stress program v1 (PORTFOLIO_STRESS_PROGRAM)")
    from sentinel.analytics.stress import PORTFOLIO_STRESS_PROGRAM

    rnd = lambda d: {k2: round(v2, 2) for k2, v2 in d.items()}  # noqa: E731
    e_stress = L.add("number", f"{book_meta['name_en']}: portfolio FCF shock, 5th percentile (joint scenario)", sb_prov, value=round(st["book"]["p05"], 2), unit="%",
                     data={"book": rnd(st["book"]), "bench": rnd(st["bench"]), "active_p05_pp": round(st["active_p05_pp"], 2), "issuers": len(order),
                           "program": PORTFOLIO_STRESS_PROGRAM, "sandbox_ms": st.get("sandbox_ms")}, key="stress:book_p05")
    e_active_p05 = L.add("number", "Active portfolio FCF shock (book − benchmark, same paths), 5th percentile (pp)", sb_prov, value=round(st["active_p05_pp"], 2), unit="pp", key="stress:active_p05")
    below = [i for i in order if iss_p05[i] < 100 * ISSUER_SHOCK_FLOOR]
    stress_out = {"paths": paths, "seed": seed, "book": rnd(st["book"]), "bench": rnd(st["bench"]), "active_p05_pp": round(st["active_p05_pp"], 2),
                  "active_p50_pp": round(st["active_p50_pp"], 2), "active_mean_pp": round(st["active_mean_pp"], 2),
                  "active_share_negative": round(st["active_share_negative"], 4),
                  "issuer_floor_pct": 100 * ISSUER_SHOCK_FLOOR,
                  "floor_binding_at_p05": {"issuers": len(below), "book_weight": round(sum(wb.get(i, 0.0) for i in below), 4),
                                           "bench_weight": round(sum(wm.get(i, 0.0) for i in below), 4)},
                  "evidence_id": e_stress, "evidence_ids": [e_stress, e_active_p05], "tail_paths": st["tail_paths"],
                  "book_histogram": {"edges": [round(v, 3) for v in st["book_histogram"]["edges"]], "counts": st["book_histogram"]["counts"]}}

    # ---- actions ----------------------------------------------------------
    actions = []
    for x in rank_actions(covered_b, covered_m, tail, n=n_actions):
        p, i = x["p"], x["id"]
        v = p["vec"]
        k = top_channel(v, scenario)
        bw = bench_raw.get(v["cik"], 0.0)
        ev_w = weight_ev(p)
        ev_bw = bench_weight_ev(p, bw)
        ev_e = exposure_ev(p, k)
        ev_p = L.add("number", f"{v['ticker']} FCF shock, 5th percentile (joint scenario, issuer marginal)", sb_prov, value=round(iss_p05[i], 1), unit="%", key=f"p05:{v['ticker']}")
        ev_c = L.add("number", f"{v['ticker']} contribution to the book's ES95 (mean over the worst 5% of book paths, pp)", sb_prov, value=round(tail[i], 2), unit="pp", key=f"tail:{v['ticker']}")
        reason = (f"북 비중 {100 * p['weight']:.2f}%[{ev_w}](벤치마크 {100 * bw:.2f}%[{ev_bw}]), {CHANNEL_META[k]['label_ko']} 노출 {v['chans'][k]:.2f}[{ev_e}], "
                  f"단독 FCF 충격 P05 {iss_p05[i]:.1f}%[{ev_p}], 북 ES95 기여 {tail[i]:.2f}%p[{ev_c}] → {VERDICT_KO[v['verdict']]}.")
        ok, why = verifier.check_text(reason, L)
        if not ok:
            raise ValueError(f"action reason failed verification: {why}: {reason}")
        actions.append({"ticker": v["ticker"], "name": v["name"], "weight_pct": round(100 * p["weight"], 2), "verdict": v["verdict"], "reason_ko": reason,
                        "p05_pct": round(iss_p05[i], 1), "evidence_ids": [ev_w, ev_bw, ev_e, ev_p, ev_c], "channel": k,
                        "tail_contrib_pp": round(tail[i], 3), "rank_tail": x["rank_tail"], "rank_active": x["rank_active"],
                        "active_exposure_pp": round(100 * x["active_exposure"], 3)})

    # ---- headline ---------------------------------------------------------
    chk = headline_chokepoint(chokepoints)
    headline = {"ko": "", "en": "", "evidence_ids": []}
    if chk is not None:
        k = next(kk for kk, m in CHANNEL_META.items() if m["chokepoint_id"] == chk["id"])
        d = float(chk["delta_pct"])
        mb = round(100 * tab[k]["material_book"], 1)
        mm = round(100 * tab[k]["material_bench"], 1)
        e_thr = L.model_value("Material-exposure threshold on channel exposure (0-1)", MATERIAL_EXPOSURE, "0-1", "portfolio lens material exposure threshold", key="const:material")
        headline = {
            "ko": (f"{CHANNEL_META[k]['label_ko']} 통항 {d:+.1f}% — {book_meta['subject_ko']} {mb:.1f}%(비중)가 이 경로에 실질 노출(노출도 ≥ {MATERIAL_EXPOSURE}), "
                   f"벤치마크 {mm:.1f}%"),
            "en": (f"{chk['name']} transits {d:+.1f}% vs 365-day baseline — {mb:.1f}% of {book_meta['subject_en']} (by weight) materially exposed to this route "
                   f"(exposure ≥ {MATERIAL_EXPOSURE}), benchmark {mm:.1f}%"),
            "evidence_ids": [x for x in (chk_ev.get(k), ch_ev[k]["material_book"], e_thr, ch_ev[k]["material_bench"]) if x],
            "channel": k, "chokepoint_id": chk["id"], "as_of": (chk.get("latest") or {}).get("date"),
        }
        if k in chk_ev:
            tagged = (f"{CHANNEL_META[k]['label_ko']} 통항 {d:+.1f}%[{chk_ev[k]}] — {mb:.1f}%[{ch_ev[k]['material_book']}](비중), "
                      f"노출도 ≥ {MATERIAL_EXPOSURE}[{e_thr}], 벤치마크 {mm:.1f}%[{ch_ev[k]['material_bench']}]")
            ok, why = verifier.check_text(tagged, L)
            if not ok:
                raise ValueError(f"headline failed verification: {why}")

    # ---- holdings / distribution / failures ----------------------------------
    holdings = []
    dist = {"REDUCE": 0, "HEDGE": 0, "HOLD": 0, "ACCUMULATE": 0}
    for p in covered_b:
        v = p["vec"]
        dist[v["verdict"]] += 1
        holdings.append({"ticker": v["ticker"], "name": v["name"], "weight_pct": round(100 * p["weight"], 4),
                         "bench_weight_pct": round(100 * bench_raw.get(v["cik"], 0.0), 4), "verdict": v["verdict"],
                         "chans": {k: round(v["chans"][k], 4) for k in CHANNELS}, "p05_pct": round(iss_p05[issuer_id(p)], 1),
                         "tail_contrib_pp": round(tail[issuer_id(p)], 4), "cik": v["cik"],
                         "filing": {kk: (v.get("filing") or {}).get(kk) for kk in ("form", "filed", "url")}})
    failures = [{"ticker": f.get("ticker") or "", "name": f.get("name"), "weight_pct": round(100 * f["weight"], 4), "side": side, "reason": f["reason"]}
                for side, fl in (("book", failed_b), ("bench", failed_m)) for f in fl]

    note_ko = (f"집계는 분석된 종목만 1로 재정규화해 계산했습니다(북 {cov_w_b * 100:.1f}%, 벤치마크 {cov_w_m * 100:.1f}% 비중). "
               "채널 노출은 각 발행사 연차보고서 기반 0–1 노출도의 가중합입니다.")
    note_en = (f"Aggregates use covered names only, renormalised to 1 (book {cov_w_b * 100:.1f}%, benchmark {cov_w_m * 100:.1f}% of weight). "
               "Channel exposure is the weighted sum of each issuer's 0–1 exposure read from its annual report.")
    return {
        "methodology_version": METHODOLOGY_VERSION,
        "generated_at": now,
        "book": {"id": book_meta["id"], "name_ko": book_meta["name_ko"], "name_en": book_meta["name_en"], "as_of": book_meta["as_of"], "filed": book_meta.get("filed"),
                 "holdings_n": book_meta["holdings_n"], "value_usd": book_meta.get("value_usd"), "covered_n": len(covered_b), "covered_weight": round(cov_w_b, 4),
                 "selected_n": len(book_sel), "selected_weight": round(sum(p["weight"] for p in book_sel), 4), "evidence_id": e_book},
        "benchmark": {"name_ko": bench_meta["name_ko"], "name_en": bench_meta["name_en"], "as_of": bench_meta["as_of"], "covered_n": len(covered_m),
                      "covered_weight": round(cov_w_m, 4), "selected_n": len(bench_sel), "label": bench_meta.get("label"), "evidence_id": e_bench},
        "scenario": {"taiwan": scenario.taiwan, "china_export": scenario.china_export, "hormuz": scenario.hormuz, "malacca": scenario.malacca, "horizon": scenario.horizon},
        "channels": channels_out,
        "stress": stress_out,
        "actions": actions,
        "verdict_distribution": dist,
        "holdings": holdings,
        "headline": headline,
        "coverage_note": {"ko": note_ko, "en": note_en,
                          "rule": book_meta.get("rule") or (f"book: top positions by weight until cumulative ≥ {BOOK_TARGET_WEIGHT:.0%} or {book_meta.get('max_names', BOOK_MAX_NAMES)} names; "
                                                            f"benchmark: top {book_meta.get('max_bench', BENCH_MAX_FILERS)} SEC annual-report filers by weight")},
        "evidence": [e.model_dump() for e in L.items],
        "failures": failures,
    }
