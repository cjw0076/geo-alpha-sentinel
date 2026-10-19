"""Run the full multi-agent pipeline for a coverage universe and publish the
results as static JSON under public/snapshots/ (used by the static deployment;
the UI switches to the live API whenever one is reachable).

    python etl/build_snapshots.py                 # default universe
    python etl/build_snapshots.py NVDA TSM        # subset
"""

from __future__ import annotations

import gzip
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sentinel.agent.runner import run_analysis  # noqa: E402
from sentinel.models import AnalysisResult, Scenario  # noqa: E402
from sentinel.http import utcnow_iso  # noqa: E402

OUT = ROOT / "public" / "snapshots"

# KIC-relevant global equities that file with the SEC (10-K / 20-F), grouped by theme.
UNIVERSE = {
    "AI semiconductors": ["NVDA", "TSM", "AMD", "AVGO", "INTC", "MU", "QCOM", "ASML", "AMAT", "LRCX", "KLAC", "TXN", "ON", "NXPI", "ARM"],
    "Platforms & devices": ["AAPL", "MSFT", "GOOGL", "AMZN", "META"],
    "EV, batteries & minerals": ["TSLA", "ALB", "MP", "FCX", "GM"],
    "Grid & data-centre power (Watts/Wires)": ["ETN", "VRT", "PWR", "GEV", "NEE", "CEG"],
    "Defense & aerospace": ["LMT", "RTX", "NOC", "GE", "BA"],
    "Industrials & energy": ["CAT", "DE", "CVX", "LIN"],
}
DEFAULT_SCENARIO = Scenario(taiwan=0.5, china_export=0.5, hormuz=0.2, malacca=0.2, horizon=2027)


def dump(path: Path, obj) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    path.write_text(raw, encoding="utf-8")
    return len(raw)


def main(tickers: list[str]) -> None:
    from sentinel.sources import macro, minerals, portwatch

    OUT.mkdir(parents=True, exist_ok=True)
    as_of, chks = portwatch.get_chokepoints()
    dump(OUT / "chokepoints.json", {"as_of": as_of, "items": [c.model_dump() for c in chks]})
    edition, mins = minerals.get_minerals()
    dump(OUT / "minerals.json", {"edition": edition, "items": [m.model_dump() for m in mins]})
    dump(OUT / "macro.json", {"items": [m.model_dump() for m in macro.get_macro()]})
    csl = ROOT / "data" / "csl_entities.json.gz"
    if csl.exists():
        shutil.copyfile(csl, OUT / "csl_entities.json.gz")

    index = []
    theme_of = {t: theme for theme, ts in UNIVERSE.items() for t in ts}
    for t in tickers:
        t0 = time.time()
        result = None
        events = []
        try:
            for item in run_analysis(t, DEFAULT_SCENARIO):
                if isinstance(item, AnalysisResult):
                    result = item
                else:
                    events.append(item.model_dump())
        except Exception as exc:  # keep going; record the failure
            print(f"[skip] {t}: {type(exc).__name__}: {exc}", flush=True)
            continue
        if result is None:
            print(f"[skip] {t}: no result ({events[-1]['title'] if events else 'no events'})", flush=True)
            continue
        d = OUT / result.ticker
        r = result.model_dump()
        size = dump(d / "analysis.json", r)
        dump(d / "company.json", r["company"])
        dump(d / "exposure.json", r["exposure"])
        dump(d / "stress.json", {"stress": r["stress"], "metrics": r["metrics"], "exposure_by_channel": r["engine"].get("exposure_by_channel", {})})
        m = {x.code: x for x in result.metrics}
        index.append({
            "ticker": result.ticker, "name": result.company.name, "theme": theme_of.get(t, "Other"),
            "cpi": m["CPI"].value, "gpi": m["GPI"].value, "srs": m["SRS"].value, "far": m["FAR"].rating, "far_score": m["FAR"].value,
            "recommendation": result.memo.recommendation, "rev_shock_p05": result.stress.revenue_shock_pct.get("p05"),
            "filing": result.company.latest_annual.model_dump() if result.company.latest_annual else None,
            "engine": result.engine.get("llm"), "generated_at": result.generated_at, "evidence_items": len(result.evidence),
        })
        print(f"[ok] {result.ticker:6} {time.time() - t0:5.1f}s  {size / 1024:7.0f} KB  CPI {m['CPI'].value:5.1f} GPI {m['GPI'].value:5.1f} SRS {m['SRS'].value:5.1f} FAR {m['FAR'].rating:>3}  {result.memo.recommendation}", flush=True)

    dump(OUT / "index.json", {"generated_at": utcnow_iso(), "scenario": DEFAULT_SCENARIO.model_dump(), "themes": list(UNIVERSE), "items": index})
    print(f"{len(index)} snapshots → {OUT}")


if __name__ == "__main__":
    args = [a.upper() for a in sys.argv[1:]]
    main(args or [t for ts in UNIVERSE.values() for t in ts])
