"""Event validation (SR 11-7-style outcomes analysis) -> public/snapshots/validation/events.json + docs/VALIDATION.md

Does filing-derived exposure, measured only with the annual report available before
each pre-declared event (sentinel/analytics/eventstudy.py: EVENTS), rank-order the
event's market-model abnormal returns? Results are written exactly as measured.

    /usr/bin/python3 etl/build_eventstudy.py                     # coverage universe + KIC 13F 10-K/20-F filers
    /usr/bin/python3 etl/build_eventstudy.py --universe base       # 40-name coverage universe only
    /usr/bin/python3 etl/build_eventstudy.py --universe base --events china_gallium_germanium_2023 --out /tmp/x.json --md ''

Raw prices are only kept in the local cache (CACHE_DIR); the outputs hold derived statistics only.
"""

from __future__ import annotations

import argparse
import importlib
import json
import math
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from sentinel.analytics import eventstudy as E  # noqa: E402
from sentinel.http import utcnow_iso  # noqa: E402
from sentinel.models import CompanyProfile, Exposure  # noqa: E402
from sentinel.sources import prices, sec  # noqa: E402

OUT_JSON = ROOT / "public" / "snapshots" / "validation" / "events.json"
OUT_MD = ROOT / "docs" / "VALIDATION.md"
SUBMISSIONS_TTL = 30 * 86400
DOC_TTL = 30 * 86400

_dates = [e["date"] for e in E.EVENTS]
PRICE_START = (date.fromisoformat(min(_dates)) - timedelta(days=430)).isoformat()
PRICE_END = (date.fromisoformat(max(_dates)) + timedelta(days=45)).isoformat()

SCOPE_NOTES = [
    "Point-in-time: each name's exposure comes from its latest original 10-K/20-F filed before the event date "
    "(filed at most 550 days earlier); no later filing, price or fundamental enters the exposure.",
    "Minerals: the channel formula uses the current USGS MCS 2026 country shares (not the edition available at each event date); "
    "only the china_export channel uses them (count of relevant minerals with China share >= 50%).",
    "Company metadata (SIC code for the sector baseline and sector-implied minerals, business-address country for the Taiwan HQ rule) "
    "is today's EDGAR submissions record.",
    "Universe is today's names (coverage universe and the latest KIC 13F), so names that were delisted before today are absent (survivorship).",
    "Each event is a single cross-section; CARs of names in the same industry are correlated, so the IC t statistic "
    "(which assumes independent names) overstates precision. The bootstrap CI resamples names, not dates.",
    "Event 6 (Red Sea) is tested on the hormuz channel, which is the model's Middle East route channel; the model has no separate Bab el-Mandeb channel.",
    "Prices: Yahoo Finance chart API adjusted close, log returns; benchmark ACWI (SPY only if ACWI lacks the needed days).",
    "Issuers that listed through a SPAC are represented before the merger by the SPAC shell's 10-K and trust-value price "
    "(same CIK and ticker history), as the declared rules include any name with a point-in-time filing and >= 120 estimation days "
    "(e.g. VRT in the 2019 event = GS Acquisition Holdings Corp).",
    "Whole-document fallback: when the existing parser finds no risk-factor heading it scores the whole annual report (capped at 400k "
    "characters); the count per event is reported as risk_section_fallback_n.",
]


def log(*a) -> None:
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# ---------------------------------------------------------------------------
# Universe
# ---------------------------------------------------------------------------

def coverage_universe() -> List[str]:
    mod = importlib.import_module("etl.build_snapshots")
    return [t for ts in mod.UNIVERSE.values() for t in ts]


def kic13f_universe() -> Optional[Dict]:
    """KIC 13F rows that map to an SEC ticker and are SEC annual-report filers (E1 `holdings.kic_13f`)."""
    try:
        from sentinel.sources import holdings
    except Exception as exc:  # module not present yet
        log(f"[universe] holdings unavailable: {type(exc).__name__}: {exc}")
        return None
    book = holdings.kic_13f(resolve=False)
    for attempt in range(5):  # the identifier map is shared with the portfolio build; retry a torn read
        try:
            holdings.resolve_book(book, log=lambda *a, **k: None, fetch=False)
            break
        except ValueError as exc:
            log(f"[universe] identifier map read retry {attempt + 1}: {exc}")
            time.sleep(3)
    rows = [r for r in book.rows if r.ticker and r.sec_filer]
    return {"as_of": book.as_of, "filed": book.filed, "source_url": book.source.url, "rows_total": len(book.rows),
            "filer_rows_n": len(rows), "tickers": list(dict.fromkeys(r.ticker for r in rows)),
            "weight_covered": round(sum(r.weight for r in rows), 4)}


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def resolve_names(tickers: List[str]) -> (Dict[str, Dict], Dict[str, str]):
    """ticker -> {cik, name, sub}; dedupe share classes by CIK (first ticker kept)."""
    names: Dict[str, Dict] = {}
    skipped: Dict[str, str] = {}
    seen_cik: Dict[str, str] = {}
    for t in tickers:
        row = sec.resolve(t)
        if row is None:
            skipped[t] = "no_sec_ticker"
            continue
        cik = row["cik"]
        if cik in seen_cik:
            skipped[t] = f"duplicate_cik_of_{seen_cik[cik]}"
            continue
        try:
            sub, _ = E.sec_json(f"https://data.sec.gov/submissions/CIK{cik}.json", ttl=SUBMISSIONS_TTL)
        except Exception as exc:
            skipped[t] = f"submissions_error: {exc}"
            continue
        seen_cik[cik] = t
        names[t] = {"cik": cik, "name": sub.get("name") or row["name"], "sub": sub}
    return names, skipped


def pit_filings(names: Dict[str, Dict], events: List[Dict]) -> Dict:
    """(ticker, event_id) -> filing dict | None."""
    out = {}
    for t, info in names.items():
        for ev in events:
            f = sec.annual_filing_before(info["sub"], info["cik"], ev["date"], E.MAX_FILING_AGE_DAYS, fetch=E.sec_json)
            out[(t, ev["id"])] = f.model_dump() if f else None
    return out


def _doc_cached(url: str) -> bool:
    from sentinel.http import _cache_path

    return _cache_path(url, None).exists()


def _drop_doc(url: str) -> None:
    from sentinel.http import _cache_path

    p = _cache_path(url, None)
    for q in (p, p.with_suffix(".meta")):
        try:
            q.unlink()
        except OSError:
            pass


def build_exposures(names: Dict[str, Dict], filings: Dict, workers: int, keep_docs: bool = False) -> Dict:
    """(cik, accession) -> exposure dict; fetches documents (paced) and parses them in worker processes.

    Documents this run downloads are removed from the shared cache once their exposure JSON is cached
    (unless keep_docs), so a large universe does not leave gigabytes of HTML behind; reruns use the
    per-filing exposure cache and need no document."""
    todo = {}
    for (t, _), f in filings.items():
        if f:
            todo.setdefault((names[t]["cik"], f["accession"]), (t, f))
    done: Dict = {}
    pending = []
    errors: Dict = {}
    n_cached = n_new = 0

    def harvest(items, block: bool):
        left = []
        for key, fut, drop_url in items:
            if not block and not fut.done():
                left.append((key, fut, drop_url))
                continue
            try:
                done[key] = fut.result()
            except Exception as exc:
                errors[key] = f"parse: {type(exc).__name__}: {exc}"
            if drop_url:
                _drop_doc(drop_url)
        return left

    with ProcessPoolExecutor(max_workers=workers) as ex:
        for i, ((cik, acc), (t, f)) in enumerate(sorted(todo.items())):
            path = E.exposure_cache_path(cik, acc)
            if path.exists():
                done[(cik, acc)] = json.loads(path.read_text())
                n_cached += 1
                continue
            was_cached = _doc_cached(f["url"])
            try:
                E._sec_get(f["url"], DOC_TTL)  # paced live fetch into the shared cache
            except Exception as exc:
                errors[(cik, acc)] = f"fetch: {exc}"
                continue
            pending.append(((cik, acc), ex.submit(E.build_exposure, t, cik, names[t]["name"], f), None if (was_cached or keep_docs) else f["url"]))
            n_new += 1
            if n_new % 25 == 0:
                log(f"[docs] fetched {n_new} new / {len(todo)} filings")
            pending = harvest(pending, block=False)
        harvest(pending, block=True)
    log(f"[exposure] {len(done)} filings ready ({n_cached} from cache, {n_new} fetched and parsed), {len(errors)} errors")
    return {"exposures": done, "errors": errors}


def load_prices(tickers: List[str]) -> Dict[str, list]:
    out = {}
    for i, t in enumerate(tickers):
        try:
            px = prices.daily_adjclose(t, PRICE_START, PRICE_END)
            if px:
                out[t] = px
        except Exception as exc:
            log(f"[prices] {t}: {type(exc).__name__}: {str(exc)[:120]}")
        if (i + 1) % 50 == 0:
            log(f"[prices] {i + 1}/{len(tickers)}")
    return out


def overlap_notes(events: List[Dict], cal: List[str]) -> List[str]:
    """Trading-day gaps between consecutive events and which windows they put inside each other (computed, not typed)."""
    out = []
    evs = sorted(events, key=lambda e: e["date"])
    e0, e1 = E.WINDOWS["estimation"]
    for a, b in zip(evs, evs[1:]):
        ga, gb = E.event_index(cal, a["date"]), E.event_index(cal, b["date"])
        gap = gb - ga
        parts = []
        p0, p1 = E.WINDOWS["primary"]
        if gap + e0 <= p1 and p0 <= gap + e1:  # [gb+e0, gb+e1] intersects [ga+p0, ga+p1]
            parts.append(f"{b['id']}'s estimation window [{e0},{e1}] includes {a['id']}'s primary window days")
        if gap <= E.WINDOWS["secondary"][1] - E.WINDOWS["primary"][0]:
            parts.append("their secondary windows overlap")
        if gap <= E.WINDOWS["primary"][1] - E.WINDOWS["primary"][0]:
            parts.append("their primary windows overlap")
        if parts:
            out.append(f"{a['id']} → {b['id']}: {gap} trading days apart; " + "; ".join(parts) + ".")
    return out


def rnd(x, k):
    return None if x is None or not np.isfinite(x) else round(float(x), k)


def run_event(ev: Dict, names: Dict, filings: Dict, expo: Dict, px: Dict, bench: Dict, all_minerals) -> (Dict, List[Dict]):
    cal_sym = E.BENCHMARK
    cal = [d for d, _ in bench[E.BENCHMARK]]
    t0 = E.event_index(cal, ev["date"])
    e0 = E.WINDOWS["estimation"][0]
    s1 = E.WINDOWS["secondary"][1]
    if t0 + e0 < 1 or t0 + s1 >= len(cal):
        cal_sym = E.BENCHMARK_FALLBACK
        cal = [d for d, _ in bench[cal_sym]]
        t0 = E.event_index(cal, ev["date"])
    r_m = E.aligned_log_returns(bench[cal_sym], cal)
    excluded = {"no_pit_filing": 0, "exposure_error": 0, "no_prices": 0, "short_estimation": 0, "incomplete_window": 0}
    rows = []
    for t, info in names.items():
        f = filings.get((t, ev["id"]))
        if not f:
            excluded["no_pit_filing"] += 1
            continue
        ed = expo["exposures"].get((info["cik"], f["accession"]))
        if ed is None:
            excluded["exposure_error"] += 1
            continue
        if t not in px:
            excluded["no_prices"] += 1
            continue
        r_i = E.aligned_log_returns(px[t], cal)
        try:
            cars = E.event_cars(r_i, r_m, t0)
        except E.InsufficientData:
            excluded["short_estimation"] += 1
            continue
        if not np.isfinite(cars["primary"]):
            excluded["incomplete_window"] += 1
            continue
        sub = info["sub"]
        profile = CompanyProfile(ticker=t, cik=info["cik"], name=info["name"], sic=sub.get("sic") or None, hq_country=sec._hq(sub))
        vals = E.exposure_values(profile, Exposure(**ed), all_minerals, ev["channel"], ev["mention_regions"])
        rows.append({"ticker": t, "x": vals["exposure"], "mention": vals["mention"], "sic2": (sub.get("sic") or "")[:2],
                     "car": cars["primary"], "car2": cars["secondary"], "beta": cars["beta"], "n_est": cars["n_est"],
                     "filing": E.filing_label(f), "fallback": (ed.get("section_title") or "").startswith("Annual report")})
    x = np.array([r["x"] for r in rows], float)
    y = np.array([r["car"] for r in rows], float)
    cs = E.cross_section(x, y, ev["expected_sign"])
    m2 = np.array([np.isfinite(r["car2"]) for r in rows], bool)
    cs2 = E.cross_section(x[m2], np.array([r["car2"] for r in rows], float)[m2], ev["expected_sign"], bootstrap=False) if m2.sum() >= 3 else {}
    sb = E.sector_baseline(x, [r["sic2"] for r in rows]) if len(rows) else np.array([])
    base_sector = E.ic_block(sb, y * 100.0, ev["expected_sign"])
    mention = np.array([r["mention"] for r in rows], float)
    base_mention = E.ic_block(mention, y * 100.0, ev["expected_sign"])
    out = {
        "id": ev["id"], "name_ko": ev["name_ko"], "name_en": ev["name_en"], "date": ev["date"], "channel": ev["channel"],
        "source_url": ev["source_url"], "crosscheck_url": ev.get("crosscheck_url"), "date_note": ev["date_note"],
        "mention_regions": ev["mention_regions"], "expected_sign": ev["expected_sign"], "benchmark_used": cal_sym,
        "n": cs["n"], "ic": rnd(cs["ic"], 4), "ic_t": rnd(cs["ic_t"], 3), "ic_p": rnd(cs["ic_p"], 4),
        "ic_ci": [rnd(v, 4) for v in cs.get("ic_ci", [float("nan")] * 2)],
        "q5q1_pp": rnd(cs["q5q1_pp"], 3), "q5q1_t": rnd(cs["q5q1_t"], 3), "q_counts": cs["q_counts"],
        "q_extreme": cs.get("q_extreme"), "q_extreme_spread_pp": rnd(cs.get("q_extreme_spread_pp", float("nan")), 3),
        "q_extreme_t": rnd(cs.get("q_extreme_t", float("nan")), 3),
        "n_secondary": int(m2.sum()), "ic_secondary": rnd(cs2.get("ic", float("nan")), 4), "ic_secondary_t": rnd(cs2.get("ic_t", float("nan")), 3),
        "q5q1_secondary_pp": rnd(cs2.get("q5q1_pp", float("nan")), 3),
        "baselines": {"sector": rnd(base_sector["ic"], 4), "mention": rnd(base_mention["ic"], 4)},
        "baselines_detail": {
            "sector": {k: (rnd(v, 4) if isinstance(v, float) else v) for k, v in base_sector.items()},
            "mention": {k: (rnd(v, 4) if isinstance(v, float) else v) for k, v in base_mention.items()},
        },
        "sign_ok": cs["sign_ok"], "significant_10pct": cs["significant_10pct"],
        "mean_car_pp": rnd(float(np.mean(y)) * 100 if len(y) else float("nan"), 3),
        "t0_date": cal[t0], "excluded": excluded,
        "risk_section_fallback_n": sum(1 for r in rows if r["fallback"]),
    }
    per = []
    for i, r in enumerate(rows):
        per.append({"ticker": r["ticker"], "event_id": ev["id"], "exposure": round(r["x"], 6), "car_pp": round(r["car"] * 100, 4),
                    "car_secondary_pp": rnd(r["car2"] * 100 if np.isfinite(r["car2"]) else float("nan"), 4),
                    "quintile": int(cs["quintile"][i]), "sector_baseline": round(float(sb[i]), 6), "mention": int(r["mention"]),
                    "filing": r["filing"]})
    return out, per


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", choices=["auto", "base", "kic13f"], default="auto")
    ap.add_argument("--events", nargs="*", default=None)
    ap.add_argument("--out", default=str(OUT_JSON))
    ap.add_argument("--md", default=str(OUT_MD), help="'' to skip the markdown report")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--keep-docs", action="store_true", help="keep newly downloaded filing documents in the cache")
    args = ap.parse_args()
    t_start = time.time()

    events = [e for e in E.EVENTS if not args.events or e["id"] in args.events]
    base = coverage_universe()
    universe = {"coverage40_n": len(base), "kic13f": None}
    tickers = list(base)
    if args.universe in ("auto", "kic13f"):
        k = kic13f_universe()
        if k is None and args.universe == "kic13f":
            raise SystemExit("holdings.kic_13f() unavailable")
        if k:
            universe["kic13f"] = {kk: v for kk, v in k.items() if kk != "tickers"} | {"filers_n": len(k["tickers"])}
            for t in k["tickers"]:
                if t not in tickers:
                    tickers.append(t)
    universe["kind"] = "coverage40+kic13f" if universe["kic13f"] else "coverage40"
    log(f"[universe] {universe['kind']}: {len(tickers)} tickers")

    names, skipped = resolve_names(tickers)
    universe.update({"tickers_n": len(tickers), "resolved_n": len(names), "skipped": skipped})
    log(f"[sec] {len(names)} names resolved, {len(skipped)} skipped")
    filings = pit_filings(names, events)
    expo = build_exposures(names, filings, args.workers, keep_docs=args.keep_docs)
    universe["exposure_errors"] = {f"{k[0]}_{k[1]}": v[:200] for k, v in sorted(expo["errors"].items())}
    px = load_prices(sorted(names))
    bench = {s: prices.daily_adjclose(s, PRICE_START, PRICE_END) for s in (E.BENCHMARK, E.BENCHMARK_FALLBACK)}
    log(f"[prices] {len(px)}/{len(names)} names with prices; ACWI {len(bench['ACWI'])} days")

    from sentinel.sources import minerals as msrc

    edition, all_minerals = msrc.get_minerals()
    date_check = None
    try:
        date_check = E.hormuz_rule_date()
    except Exception as exc:
        log(f"[portwatch] date check unavailable: {exc}")

    ev_out, per_issuer = [], {}
    for ev in events:
        o, per = run_event(ev, names, filings, expo, px, bench, all_minerals)
        if ev["id"] == "hormuz_transit_collapse_2026":
            o["date_check"] = {"rule": "first day PortWatch chokepoint6 7-day mean < 50% of the prior 365-day mean",
                               "recomputed": date_check, "matches_declared": bool(date_check and date_check["date"] == ev["date"])}
        ev_out.append(o)
        for r in per:
            per_issuer.setdefault(r.pop("ticker"), []).append(r)
        log(f"[event] {ev['id']:32s} n={o['n']:4d} IC={o['ic']} t={o['ic_t']} Q5-Q1={o['q5q1_pp']}pp "
            f"sector={o['baselines']['sector']} mention={o['baselines']['mention']} excluded={o['excluded']}")

    doc = {
        "methodology_version": E.METHODOLOGY_VERSION, "generated_at": utcnow_iso(), "declared_at": E.DECLARED_AT,
        "windows": {k: list(v) for k, v in E.WINDOWS.items()}, "benchmark": E.BENCHMARK, "benchmark_fallback": E.BENCHMARK_FALLBACK,
        "min_estimation_obs": E.MIN_ESTIMATION_OBS, "max_filing_age_days": E.MAX_FILING_AGE_DAYS,
        "bootstrap": {"resamples": E.BOOTSTRAP_RESAMPLES, "seed": E.BOOTSTRAP_SEED}, "significance_level": E.SIGNIFICANCE_LEVEL,
        "universe": universe, "minerals_edition": edition,
        "events": ev_out, "summary": E.summarize(ev_out),
        "per_issuer": {t: per_issuer[t] for t in sorted(per_issuer)},
        "scope_notes": SCOPE_NOTES + overlap_notes(events, [d for d, _ in bench[E.BENCHMARK]]),
        "sources": {"market_data": prices.SOURCE_NOTE, "filings": "SEC EDGAR submissions and primary 10-K/20-F documents (public domain)",
                    "minerals": f"USGS {edition}", "portwatch": "IMF PortWatch daily chokepoint transits (attribution)"},
        "runtime_s": round(time.time() - t_start, 1),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log(f"[out] {out} ({out.stat().st_size / 1024:.0f} KB), runtime {doc['runtime_s']} s")
    if args.md:
        Path(args.md).write_text(render_markdown(doc), encoding="utf-8")
        log(f"[out] {args.md}")
    s = doc["summary"]
    log(f"[summary] {json.dumps(s)}")


# ---------------------------------------------------------------------------
# docs/VALIDATION.md
# ---------------------------------------------------------------------------

def _f(v, k=3, signed=True):
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "—"
    return f"{v:+.{k}f}" if signed else f"{v:.{k}f}"


def render_markdown(doc: Dict) -> str:
    s, u = doc["summary"], doc["universe"]
    L = []
    L.append("# Validation — event study of filing-derived exposure (methodology evt-1.0)")
    L.append("")
    L.append(f"_Generated by `etl/build_eventstudy.py` at {doc['generated_at']}. Events, windows, benchmark, exposure variables and "
             f"baselines were declared in `sentinel/analytics/eventstudy.py` at {doc['declared_at']}, before any return was computed, "
             "and were not changed afterwards. Numbers below are as measured._")
    L.append("")
    L.append("## Purpose")
    L.append("")
    L.append("SR 11-7-style outcomes analysis of the exposure model: does a name's channel exposure, computed only from the annual "
             "report it had filed before an event, rank-order that event's abnormal returns? Expected sign for every event: "
             "negative (higher exposure, lower CAR).")
    L.append("")
    L.append("## Design")
    L.append("")
    L.append(f"* **Point-in-time exposure.** For each name and event: the latest original 10-K/20-F with filing date before the event "
             f"(at most {doc['max_filing_age_days']} days old) → risk-factor section → `analyze_exposure` → `channel_exposures(...)[channel]`; "
             "for the `weaponized` channel the 4W weaponized score / 100.")
    L.append(f"* **Abnormal returns.** Log returns of adjusted close; market model (OLS α, β) on trading days "
             f"[{doc['windows']['estimation'][0]}, {doc['windows']['estimation'][1]}] (≥ {doc['min_estimation_obs']} observations) against "
             f"{doc['benchmark']}; CAR[{doc['windows']['primary'][0]},+{doc['windows']['primary'][1]}] primary, "
             f"CAR[{doc['windows']['secondary'][0]},+{doc['windows']['secondary'][1]}] secondary.")
    L.append(f"* **Statistics per event.** Spearman rank IC(exposure, CAR) with t = IC·√((n−2)/(1−IC²)) and two-sided p from Student t(n−2); "
             f"bootstrap 95% CI ({doc['bootstrap']['resamples']} resamples of names, seed {doc['bootstrap']['seed']}); "
             "Q5−Q1 mean CAR spread (pp, quintiles by exposure rank) with Welch t.")
    L.append("* **Naive baselines** on the same names and CARs: *sector* = mean exposure of the other same-SIC2 names "
             "(no peer → mean of all other names); *mention* = raw count of the event's geography in the risk-factor section.")
    k = u.get("kic13f")
    uni = f"{u['coverage40_n']}-name coverage universe"
    if k:
        uni += (f" ∪ KIC Form 13F-HR holdings (period {k['as_of']}, filed {k['filed']}) that map to an SEC ticker and file annual reports "
                f"({k.get('filer_rows_n', k['filers_n'])} rows, {k['filers_n']} distinct tickers, {k['weight_covered'] * 100:.1f}% of 13F value)")
    L.append(f"* **Universe.** {uni}; {u['resolved_n']} distinct issuers after de-duplicating share classes. "
             "Names without a point-in-time filing, with < 120 estimation days or with an incomplete event window are excluded per event and counted.")
    L.append("")
    L.append("## Events (pre-declared)")
    L.append("")
    L.append("| # | Event | t=0 | Channel | Source |")
    L.append("|---|---|---|---|---|")
    for i, e in enumerate(doc["events"], 1):
        L.append(f"| {i} | {e['name_en']} / {e['name_ko']} | {e['date']} | {e['channel']} | [source]({e['source_url']}) |")
    L.append("")
    for i, e in enumerate(doc["events"], 1):
        L.append(f"{i}. `{e['id']}` — {e['date_note']}")
    L.append("")
    L.append("## Results per event (primary window CAR[−1,+5])")
    L.append("")
    L.append("| Event | n | IC | t | p | 95% CI | Q5−Q1 (pp) | Welch t | IC [−1,+20] | Q5−Q1 [−1,+20] (pp) | sector IC | mention IC | sign as expected |")
    L.append("|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|---|")
    for e in doc["events"]:
        ci = e["ic_ci"]
        L.append(f"| {e['id']} | {e['n']} | {_f(e['ic'])} | {_f(e['ic_t'], 2)} | {_f(e['ic_p'], 3, False)} | [{_f(ci[0], 2)}, {_f(ci[1], 2)}] | "
                 f"{_f(e['q5q1_pp'], 2)} | {_f(e['q5q1_t'], 2)} | {_f(e['ic_secondary'])} | {_f(e['q5q1_secondary_pp'], 2)} | "
                 f"{_f(e['baselines']['sector'])} | {_f(e['baselines']['mention'])} | {'yes' if e['sign_ok'] else 'no'} |")
    L.append("")
    tied = [e for e in doc["events"] if e.get("q5q1_pp") is None and e.get("q_extreme")]
    for e in tied:
        lo, hi = e["q_extreme"]
        L.append(f"`{e['id']}`: Q5−Q1 is undefined under the declared tie rule (quintile counts Q1..Q5 = {e['q_counts']}; tied lowest "
                 f"exposures share one quintile). Supplementary, added after the 40-name run and not part of the declaration: "
                 f"Q{hi}−Q{lo} (top minus bottom non-empty quintile) = {_f(e['q_extreme_spread_pp'], 2)} pp, Welch t {_f(e['q_extreme_t'], 2)}.")
        L.append("")
    L.append("Exclusions per event (no point-in-time filing / exposure error / no prices / < 120 estimation days / incomplete window), "
             "and the number of included filings where the parser found no risk-factor heading and scored the whole document (capped at 400k characters):")
    L.append("")
    for e in doc["events"]:
        x = e["excluded"]
        L.append(f"* `{e['id']}`: {x['no_pit_filing']} / {x['exposure_error']} / {x['no_prices']} / {x['short_estimation']} / {x['incomplete_window']}"
                 f"; whole-document fallback {e.get('risk_section_fallback_n', 0)}; benchmark {e['benchmark_used']}")
    hz = next((e for e in doc["events"] if e.get("date_check")), None)
    if hz and hz["date_check"].get("recomputed"):
        r = hz["date_check"]["recomputed"]
        L.append("")
        L.append(f"Hormuz date rule recomputed from PortWatch at build time: {r['date']} (7-day mean {r['mean_7d']} vs 365-day baseline "
                 f"{r['baseline_365d']}, ratio {r['ratio']}); matches the declared date: {'yes' if hz['date_check']['matches_declared'] else 'no'}.")
    L.append("")
    L.append("## Baselines and summary")
    L.append("")
    L.append("| Measure | events | IC sign as expected | significant at 10% (two-sided) | mean IC |")
    L.append("|---|---:|---:|---:|---:|")
    L.append(f"| Model exposure | {s['events_n']} | {s['sign_ok_n']} | {s['significant_10pct_n']} (expected sign: {s['significant_10pct_expected_sign_n']}) | {_f(s['mean_ic'])} |")
    L.append(f"| Sector-only baseline | {s['events_n']} | {s['baseline_sector_sign_ok_n']} | {s['baseline_sector_significant_10pct_n']} | {_f(s['baseline_sector_mean_ic'])} |")
    L.append(f"| Mention-only baseline | {s['events_n']} | {s['baseline_mention_sign_ok_n']} | {s['baseline_mention_significant_10pct_n']} | {_f(s['baseline_mention_mean_ic'])} |")
    L.append("")
    L.append(f"Mean IC on the secondary window: {_f(s['mean_ic_secondary'])}; mean Q5−Q1 (primary): {_f(s['mean_q5q1_pp'], 2)} pp.")
    L.append("")
    L.append("## Scope notes")
    L.append("")
    for n in doc["scope_notes"]:
        L.append(f"* {n}")
    L.append("* Sample size: with n names per event, |IC| must exceed roughly 1.65/√(n−2) to reach 10% significance "
             "(≈ 0.27 at n = 40, ≈ 0.13 at n = 150).")
    L.append("")
    L.append("## Reproduce")
    L.append("")
    L.append("```")
    L.append("/usr/bin/python3 etl/build_eventstudy.py            # rebuilds public/snapshots/validation/events.json and this file")
    L.append("/usr/bin/python3 -m pytest -q tests/test_eventstudy.py")
    L.append("```")
    L.append("")
    L.append("Inputs are cached under `SENTINEL_CACHE_DIR` (SEC submissions and documents, per-filing exposure JSON, Yahoo price payloads); "
             "a rerun with a warm cache makes no SEC document or price requests. Any event's IC can be recomputed from `per_issuer` "
             "(Spearman of `exposure` vs `car_pp` over the rows with that `event_id`). Raw prices are not published; `per_issuer` "
             "holds only CARs (pp) and exposures.")
    L.append("")
    return "\n".join(L)


if __name__ == "__main__":
    main()
