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


# Joint (common-shock) portfolio program. Per path, each channel's event draw, depth Beta(2,3),
# duration U(0.25,1) and cost multiplier Beta(2,5) are drawn ONCE and shared by every issuer,
# in the same order as STRESS_PROGRAM; an issuer's own event threshold p_k still depends on its
# base year. Hence each issuer's marginal FCF-shock distribution is exactly the one run_stress
# gives it (same seed), and a one-issuer book reproduces run_stress. In the weighted sums each
# issuer's shock is floored at `issuer_floor` (default -100%: a holding loses at most its whole
# base FCF), because FCF shocks are relative to |base FCF| and are unbounded when FCF is near zero.
PORTFOLIO_STRESS_PROGRAM = '''\
# Geo-Alpha Sentinel portfolio stress program v1 — joint (common-shock) Monte Carlo
rng = np.random.default_rng(seed)
channels = ["taiwan", "china_export", "hormuz", "malacca"]
n = len(base_revenue)
E = np.array(exposure, dtype=float).reshape(n, 4)
years = np.maximum(1, horizon - np.array(base_year, dtype=float))
rev0 = np.array(base_revenue, dtype=float)
cogs0 = np.array(base_cogs, dtype=float)
gm = np.array(gross_margin, dtype=float)
fcf0 = np.array(base_fcf, dtype=float)
wb = np.array(w_book, dtype=float)
wm = np.array(w_bench, dtype=float)
U, D, T, B = [], [], [], []
for k in channels:                                           # common shock: one draw per path and channel
    U.append(rng.random(paths))                              # event occurrence
    D.append(rng.beta(2.0, 3.0, paths))                      # depth
    T.append(rng.uniform(0.25, 1.0, paths))                  # share of the year disrupted
    B.append(rng.beta(2.0, 5.0, paths))                      # input-cost multiplier

def shocks(lo, hi):
    # FCF shock (fraction of |base FCF|) of issuers lo..hi-1 on every path, shape (paths, hi - lo)
    rev_shock = np.zeros((paths, hi - lo))
    cost_shock = np.zeros((paths, hi - lo))
    for j in range(4):
        k = channels[j]
        s = severity[k]
        e = E[lo:hi, j][None, :]
        p_event = np.minimum(0.95, s * (0.55 + 0.10 * years[lo:hi]))[None, :]
        hit = U[j][:, None] < p_event
        depth = D[j][:, None] * max_revenue_loss[k] * e * (0.5 + 0.5 * s)
        rev_shock = rev_shock - hit * depth * T[j][:, None]
        cost_shock = cost_shock + hit * B[j][:, None] * input_cost_beta[k] * e * s
    rev_shock = np.clip(rev_shock, -0.95, 0.0)
    lost_revenue = -rev_shock * rev0[lo:hi][None, :]
    extra_cost = cost_shock * cogs0[lo:hi][None, :]
    fcf = fcf0[lo:hi][None, :] - lost_revenue * gm[lo:hi][None, :] - extra_cost
    return (fcf - fcf0[lo:hi][None, :]) / np.abs(fcf0[lo:hi])[None, :]

book = np.zeros(paths)
bench = np.zeros(paths)
for lo in range(0, n, chunk):
    hi = min(n, lo + chunk)
    x = np.maximum(shocks(lo, hi), issuer_floor)              # a holding loses at most its whole base FCF
    book = book + x @ wb[lo:hi]
    bench = bench + x @ wm[lo:hi]
active = book - bench
k_tail = int(math.ceil(0.05 * paths))
tail = np.argsort(book, kind="stable")[:k_tail]              # the book's worst 5% of paths
tail_contrib = np.zeros(n)
iss_p05 = np.zeros(n)
iss_p50 = np.zeros(n)
for lo in range(0, n, chunk):
    hi = min(n, lo + chunk)
    x = shocks(lo, hi)
    tail_contrib[lo:hi] = wb[lo:hi] * np.maximum(x[tail, :], issuer_floor).mean(axis=0)  # sums to the book's ES95
    iss_p05[lo:hi] = np.percentile(x, 5, axis=0)              # issuer marginals are reported without the floor
    iss_p50[lo:hi] = np.percentile(x, 50, axis=0)

def pct(x, q):
    return float(np.percentile(x, q))

def summary(x):
    srt = np.sort(x)
    return {"p05": pct(x, 5) * 100, "p50": pct(x, 50) * 100, "mean": float(x.mean()) * 100,
            "es95": float(srt[:k_tail].mean()) * 100, "p_below_20": float((x < -0.20).mean()) * 100}

counts, edges = np.histogram(book * 100.0, bins=24)
result = {
    "book": summary(book), "bench": summary(bench),
    "active_p05_pp": pct(active, 5) * 100, "active_p50_pp": pct(active, 50) * 100, "active_mean_pp": float(active.mean()) * 100,
    "active_share_negative": float((active < 0).mean()), "active_share_zero": float((active == 0).mean()),
    "tail_paths": k_tail,
    "tail_contrib_pp": tail_contrib * 100, "issuer_p05_pct": iss_p05 * 100, "issuer_p50_pct": iss_p50 * 100,
    "book_histogram": {"edges": [float(v) for v in edges], "counts": [float(v) for v in counts]},
}
'''
