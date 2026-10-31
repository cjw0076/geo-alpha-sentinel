"""Portfolio lens snapshots: KIC's 13F book (and the 40-name coverage universe, equal weight)
versus an MSCI ACWI ex Korea proxy, channel by channel, with a joint-scenario Monte Carlo.

    python etl/build_portfolio.py                      # latest 13F-HR, latest ACWI NPORT-P
    python etl/build_portfolio.py --accession 0001193125-26-345356 --max-names 300
    python etl/build_portfolio.py --bench-period 2026-06-30   # benchmark period aligned to the book

Writes public/snapshots/portfolio/kic13f.json and public/snapshots/portfolio/coverage40.json.
SEC requests are limited to 4/second for this process (sentinel.sources.holdings).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sentinel.analytics import portfolio as P  # noqa: E402
from sentinel.config import MC_PATHS, MC_SEED  # noqa: E402
from sentinel.http import utcnow_iso  # noqa: E402
from sentinel.models import Provenance  # noqa: E402
from sentinel.sources import holdings as H  # noqa: E402

OUT = ROOT / "public" / "snapshots" / "portfolio"
T0 = time.time()


def log(msg: str) -> None:
    print(f"[{time.time() - T0:7.1f}s] {msg}", flush=True)


def dump(path: Path, obj) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    path.write_text(raw, encoding="utf-8")
    return len(raw)


def chokepoint_items():
    from sentinel import service

    try:
        as_of, chks = service.chokepoints()
        if chks:
            return as_of, [c.model_dump() for c in chks], "live"
    except Exception as exc:  # fall back to the committed snapshot
        log(f"PortWatch live fetch failed ({type(exc).__name__}); using public/snapshots/chokepoints.json")
    d = json.loads((ROOT / "public" / "snapshots" / "chokepoints.json").read_text())
    if d.get("items"):
        return d.get("as_of"), d["items"], "snapshot"
    try:  # last resort: the committed snapshot (the working-tree copy may have been rebuilt while PortWatch was unavailable)
        import subprocess

        raw = subprocess.run(["git", "-C", str(ROOT), "show", "HEAD:public/snapshots/chokepoints.json"], capture_output=True, check=True, timeout=30).stdout
        d = json.loads(raw)
        if d.get("items"):
            return d.get("as_of"), d["items"], "snapshot (git HEAD)"
    except Exception:
        pass
    return None, [], "unavailable"


def coverage_universe():
    from etl.build_snapshots import UNIVERSE

    return [t for ts in UNIVERSE.values() for t in ts]


def summary(view: dict) -> None:
    b, m = view["book"], view["benchmark"]
    log(f"{b['id']}: covered {b['covered_n']} names, covered_weight {b['covered_weight']:.4f} (selected {b['selected_n']}, {b['selected_weight']:.4f}); "
        f"benchmark covered {m['covered_n']}, covered_weight {m['covered_weight']:.4f}")
    for c in view["channels"]:
        log(f"  {c['id']:13} book {c['book_pct']:6.2f}%  bench {c['bench_pct']:6.2f}%  active {c['active_pp']:+6.2f}pp  effN {c['effective_n']:6.1f}  "
            f"top: {', '.join(t['ticker'] for t in c['top'][:3])}")
    s = view["stress"]
    log(f"  stress book {s['book']}  bench {s['bench']}  active_p05 {s['active_p05_pp']}pp")
    for a in view["actions"]:
        log(f"  action {a['ticker']:6} {a['verdict']:10} w {a['weight_pct']:5.2f}%  p05 {a['p05_pct']:7.1f}%  {a['reason_ko']}")
    log(f"  verdicts {view['verdict_distribution']}  failures {len(view['failures'])}  evidence {len(view['evidence'])}")
    log(f"  headline: {view['headline']['ko']} | {view['headline']['en']}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--accession", help="KIC 13F-HR accession (default: latest)")
    ap.add_argument("--max-names", type=int, default=P.BOOK_MAX_NAMES, help="cap on analysed book names (default 300; coverage target 90%% of weight)")
    ap.add_argument("--max-bench", type=int, default=P.BENCH_MAX_FILERS, help="benchmark: top-N SEC annual-report filers (default 500)")
    ap.add_argument("--bench-accession", help="ACWI NPORT-P accession (default: latest)")
    ap.add_argument("--bench-period", help="use the latest ACWI NPORT-P with period end on or before this date (YYYY-MM-DD)")
    ap.add_argument("--paths", type=int, default=MC_PATHS)
    ap.add_argument("--seed", type=int, default=MC_SEED)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--skip-coverage40", action="store_true")
    ap.add_argument("--vectors-cache", help="optional JSON file: reuse issuer vectors stored there, store newly computed ones")
    args = ap.parse_args()

    H.install_sec_rate_limit(4.0)
    log("loading KIC 13F-HR and ACWI NPORT-P (identifier map: data/identifier_map.json)")
    kic = H.kic_13f(args.accession)
    bench = H.acwi_proxy(accession=args.bench_accession, period=args.bench_period)
    for b in (kic, bench):
        log(f"{b.name} as of {b.as_of} (filed {b.filed}): {len(b.rows)} rows, ${b.total_value_usd / 1e9:,.2f}B")
        for n in b.notes:
            log(f"  {n}")
    kic_pos, bench_pos = P.positions(kic), P.positions(bench)
    book_sel = P.select_book(kic_pos, max_names=args.max_names)
    bench_sel = P.select_bench(bench_pos, max_filers=args.max_bench)
    cov40 = [] if args.skip_coverage40 else coverage_universe()
    log(f"selected: book {len(book_sel)} positions ({sum(p['weight'] for p in book_sel):.4f} of weight), benchmark {len(bench_sel)} filers "
        f"({sum(p['weight'] for p in bench_sel):.4f}), coverage universe {len(cov40)}")

    as_of_chk, chks, chk_mode = chokepoint_items()
    log(f"chokepoints as of {as_of_chk} ({chk_mode})")
    from sentinel import service

    try:
        service.minerals()
        from sentinel.sources import sanctions

        sanctions.screen_many(["warm-up"])
    except Exception:
        pass
    tickers = [p["ticker"] for p in book_sel if p.get("ticker") and p.get("sec_filer")] + [p["ticker"] for p in bench_sel] + cov40
    tickers = list(dict.fromkeys(tickers))
    log(f"issuer vectors for {len(tickers)} tickers ({args.workers} workers)")
    cached = {}
    if args.vectors_cache and Path(args.vectors_cache).exists():
        cached = {t: v for t, v in json.loads(Path(args.vectors_cache).read_text()).items() if "error" not in v}
        log(f"reusing {sum(1 for t in tickers if t in cached)} issuer vectors from {args.vectors_cache}")
    vectors = {t: cached[t] for t in tickers if t in cached}
    vectors.update(P.issuer_vectors([t for t in tickers if t not in cached], max_workers=args.workers, log=log))
    if args.vectors_cache:
        Path(args.vectors_cache).write_text(json.dumps({**cached, **vectors}, ensure_ascii=False))
    log(f"issuer vectors done: {sum(1 for v in vectors.values() if 'error' not in v)} ok, {sum(1 for v in vectors.values() if 'error' in v)} failed; "
        f"SEC requests this run {H.sec_requests_made()}")

    bench_meta = {"name_ko": "ACWI ex Korea 대용", "name_en": "MSCI ACWI ex Korea proxy", "as_of": bench.as_of, "source": bench.source, "label": H.BENCH_LABEL}
    kic_meta = {"id": "kic13f", "name_ko": "KIC 미국 상장주식 (13F)", "name_en": "KIC US equities (13F)", "as_of": kic.as_of, "filed": kic.filed,
                "holdings_n": len(kic.rows), "value_usd": round(kic.total_value_usd, 2), "subject_ko": "KIC 미국 보유분의", "subject_en": "KIC's US holdings",
                "source": kic.source, "weight_basis": "13f", "max_names": args.max_names, "max_bench": args.max_bench}
    view = P.build_view(kic_meta, bench_meta, book_sel, bench_sel, bench_pos, vectors, chks, paths=args.paths, seed=args.seed)
    view["chokepoints_as_of"] = as_of_chk
    size = dump(OUT / "kic13f.json", view)
    log(f"wrote {OUT / 'kic13f.json'} ({size / 1024:.0f} KB)")
    summary(view)

    if cov40:
        names = {t: (vectors.get(t) or {}).get("name", t) for t in cov40}
        cov_pos = P.equal_weight_positions(cov40, names)
        cov_meta = {"id": "coverage40", "name_ko": "커버리지 40종목 (동일가중)", "name_en": "Coverage universe, 40 names (equal weight)",
                    "as_of": utcnow_iso()[:10], "filed": None, "holdings_n": len(cov40), "value_usd": None,
                    "subject_ko": "커버리지 40종목의", "subject_en": "the 40-name coverage universe",
                    "source": Provenance(source="model", title="Geo-Alpha Sentinel coverage universe (etl/build_snapshots.py UNIVERSE), equal weight",
                                         url="https://github.com/cjw0076/geo-alpha-sentinel/blob/main/etl/build_snapshots.py", retrieved_at=utcnow_iso(),
                                         license="Apache-2.0", locator="UNIVERSE"),
                    "weight_basis": "equal", "rule": f"book: the {len(cov40)}-name coverage universe, equal weight; benchmark: top {args.max_bench} SEC annual-report filers by weight"}
        view40 = P.build_view(cov_meta, bench_meta, cov_pos, bench_sel, bench_pos, vectors, chks, paths=args.paths, seed=args.seed)
        view40["chokepoints_as_of"] = as_of_chk
        size = dump(OUT / "coverage40.json", view40)
        log(f"wrote {OUT / 'coverage40.json'} ({size / 1024:.0f} KB)")
        summary(view40)
    log(f"done in {time.time() - T0:.0f}s")


if __name__ == "__main__":
    main()
