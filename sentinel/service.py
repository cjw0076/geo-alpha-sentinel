"""Shared, cached building blocks behind the HTTP API."""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from sentinel.analytics import metrics as M
from sentinel.analytics.exposure import analyze_exposure
from sentinel.analytics.stress import run_stress
from sentinel.ledger import Ledger
from sentinel.models import Chokepoint, CompanyProfile, Exposure, Metric, Mineral, SanctionsScreen, Scenario, StressResult
from sentinel.sources import filings, sec


class TTLCache:
    def __init__(self, ttl: float, maxsize: int = 256) -> None:
        self.ttl, self.maxsize = ttl, maxsize
        self._d: Dict[Any, Tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get_or_set(self, key: Any, fn: Callable[[], Any]) -> Any:
        now = time.time()
        with self._lock:
            hit = self._d.get(key)
            if hit and now - hit[0] < self.ttl:
                return hit[1]
        val = fn()
        with self._lock:
            if len(self._d) >= self.maxsize:
                oldest = min(self._d, key=lambda k: self._d[k][0])
                self._d.pop(oldest, None)
            self._d[key] = (now, val)
        return val

    def get(self, key: Any) -> Optional[Any]:
        with self._lock:
            hit = self._d.get(key)
            if hit and time.time() - hit[0] < self.ttl:
                return hit[1]
        return None

    def set(self, key: Any, val: Any) -> None:
        with self._lock:
            self._d[key] = (time.time(), val)


_profiles = TTLCache(3600)
_exposures = TTLCache(6 * 3600)
_contexts = TTLCache(1800)


def profile(ticker: str) -> CompanyProfile:
    t = sec.normalize_ticker(ticker)
    return _profiles.get_or_set(t, lambda: sec.company_profile(t))


def exposure(ticker: str) -> Exposure:
    p = profile(ticker)
    if p.latest_annual is None:
        raise LookupError(f"{p.ticker}: no 10-K/20-F on EDGAR yet")

    def build() -> Exposure:
        import re

        e = analyze_exposure(p.ticker, filings.risk_section(p.latest_annual))
        own = {w.lower() for w in re.findall(r"[A-Za-z]{3,}", p.name)}
        e.entities_named = [x for x in e.entities_named if not ({w.lower() for w in re.findall(r"[A-Za-z]{3,}", x)} & own)]
        return e

    return _exposures.get_or_set((p.ticker, p.latest_annual.accession), build)


def chokepoints() -> Tuple[str, List[Chokepoint]]:
    from sentinel.sources import portwatch

    return portwatch.get_chokepoints()


def minerals() -> Tuple[str, List[Mineral]]:
    from sentinel.sources import minerals as src

    return src.get_minerals()


def stress_context(ticker: str) -> Dict[str, Any]:
    """Everything the slider fast-path needs, cached per ticker."""

    def build() -> Dict[str, Any]:
        p = profile(ticker)
        e = exposure(ticker)
        try:
            _, allm = minerals()
        except Exception:
            allm = []
        rel = M.relevant_minerals(p, e, allm)
        try:
            _, chks = chokepoints()
        except Exception:
            chks = []
        screens: List[SanctionsScreen] = []
        try:
            from sentinel.sources import sanctions

            if e.entities_named:
                screens = sanctions.screen_many(e.entities_named[:6])
        except Exception:
            screens = []
        chans = M.channel_exposures(p, e, [m for m, _ in rel])
        rev = M.latest(p, "revenue") or 1.0
        gp = M.latest(p, "gross_profit")
        gm = gp / rev if gp and rev else 0.5
        cfo, capex = M.latest(p, "cfo"), M.latest(p, "capex")
        fcf = (cfo - capex) if cfo is not None and capex is not None else 0.1 * rev
        base_year = p.fundamentals["revenue"][-1].fy if p.fundamentals.get("revenue") else 2025
        return {"profile": p, "exposure": e, "minerals": rel, "chokepoints": chks, "screens": screens, "chans": chans,
                "rev": rev, "gm": gm, "fcf": fcf, "base_year": base_year}

    return _contexts.get_or_set(sec.normalize_ticker(ticker), build)


def stress_and_metrics(ticker: str, scenario: Scenario) -> Tuple[StressResult, List[Metric], Dict[str, float]]:
    c = stress_context(ticker)
    st = run_stress(scenario, c["chans"], base_revenue=c["rev"], gross_margin=c["gm"], base_fcf=c["fcf"], base_year=c["base_year"])
    mets = M.compute_metrics(c["profile"], c["exposure"], c["minerals"], c["chokepoints"], c["screens"], st, Ledger(), {})
    return st, mets, c["chans"]
