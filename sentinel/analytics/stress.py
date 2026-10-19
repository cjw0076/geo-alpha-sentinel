"""Scenario stress test (Monte Carlo) executed in the deterministic sandbox.

The program text below is what runs; it is returned verbatim in
`StressResult.code` so a reviewer can re-run it and get identical numbers
(fixed seed).
"""

from __future__ import annotations

from typing import Any, Dict

from sentinel import sandbox
from sentinel.config import MC_PATHS, MC_SEED
from sentinel.models import Scenario, StressResult

# Scenario library v1 (documented in docs/METHODOLOGY.md). Revenue-loss caps are the
# share of revenue a fully exposed company loses while a channel is disrupted;
# cost betas are extra cost of goods sold as a share of COGS.
MAX_REVENUE_LOSS = {"taiwan": 0.65, "china_export": 0.30, "hormuz": 0.12, "malacca": 0.20}
INPUT_COST_BETA = {"taiwan": 0.10, "china_export": 0.08, "hormuz": 0.06, "malacca": 0.04}

STRESS_PROGRAM = '''\
# Geo-Alpha Sentinel stress program v1 — deterministic (seeded) Monte Carlo
rng = np.random.default_rng(seed)
channels = ["taiwan", "china_export", "hormuz", "malacca"]
years = max(1, horizon - base_year)
rev_shock = np.zeros(paths)
cost_shock = np.zeros(paths)
for k in channels:
    s = severity[k]
    e = exposure[k]
    p_event = min(0.95, s * (0.55 + 0.10 * years))          # chance of a material disruption by the horizon
    hit = rng.random(paths) < p_event
    depth = rng.beta(2.0, 3.0, paths) * max_revenue_loss[k] * e * (0.5 + 0.5 * s)
    duration = rng.uniform(0.25, 1.0, paths)                 # share of the year disrupted
    rev_shock = rev_shock - hit * depth * duration
    cost_shock = cost_shock + hit * rng.beta(2.0, 5.0, paths) * input_cost_beta[k] * e * s
rev_shock = np.clip(rev_shock, -0.95, 0.0)
lost_revenue = -rev_shock * base_revenue
extra_cost = cost_shock * base_cogs
fcf = base_fcf - lost_revenue * gross_margin - extra_cost
fcf_shock = (fcf - base_fcf) / abs(base_fcf)
counts, edges = np.histogram(rev_shock * 100.0, bins=24)
def pct(x, q):
    return float(np.percentile(x, q))
result = {
    "revenue_shock_pct": {"p05": pct(rev_shock, 5) * 100, "p50": pct(rev_shock, 50) * 100, "p95": pct(rev_shock, 95) * 100, "mean": float(rev_shock.mean()) * 100},
    "fcf_shock_pct": {"p05": pct(fcf_shock, 5) * 100, "p50": pct(fcf_shock, 50) * 100, "p95": pct(fcf_shock, 95) * 100, "mean": float(fcf_shock.mean()) * 100},
    "prob_fcf_drawdown_gt_20": float((fcf_shock < -0.20).mean()),
    "histogram": {"edges": [float(v) for v in edges], "counts": [float(v) for v in counts]},
}
'''


def run_stress(
    scenario: Scenario,
    exposure_by_channel: Dict[str, float],
    base_revenue: float,
    gross_margin: float,
    base_fcf: float,
    base_year: int,
    paths: int = MC_PATHS,
    seed: int = MC_SEED,
) -> StressResult:
    if base_fcf == 0:
        base_fcf = 0.01 * base_revenue
    inputs: Dict[str, Any] = {
        "seed": seed,
        "paths": paths,
        "horizon": scenario.horizon,
        "base_year": base_year,
        "severity": {"taiwan": scenario.taiwan, "china_export": scenario.china_export, "hormuz": scenario.hormuz, "malacca": scenario.malacca},
        "exposure": {k: round(float(v), 4) for k, v in exposure_by_channel.items()},
        "max_revenue_loss": MAX_REVENUE_LOSS,
        "input_cost_beta": INPUT_COST_BETA,
        "base_revenue": float(base_revenue),
        "base_cogs": float(base_revenue * (1 - gross_margin)),
        "gross_margin": float(gross_margin),
        "base_fcf": float(base_fcf),
    }
    out = sandbox.run(STRESS_PROGRAM, inputs)
    r = out["result"]
    rnd = lambda d: {k: round(v, 2) for k, v in d.items()}  # noqa: E731
    return StressResult(
        paths=paths,
        seed=seed,
        base_revenue=base_revenue,
        base_fcf=base_fcf,
        revenue_shock_pct=rnd(r["revenue_shock_pct"]),
        fcf_shock_pct=rnd(r["fcf_shock_pct"]),
        prob_fcf_drawdown_gt_20=round(r["prob_fcf_drawdown_gt_20"], 4),
        histogram={"edges": [round(v, 3) for v in r["histogram"]["edges"]], "counts": r["histogram"]["counts"]},
        code=STRESS_PROGRAM,
        inputs={**inputs, "sandbox_ms": out["ms"]},
    )
