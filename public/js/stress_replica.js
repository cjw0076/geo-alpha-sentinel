// Browser/Node replica of the Python stress program (sentinel/analytics/stress.py,
// STRESS_PROGRAM) and of the SRS / FAR formulas (sentinel/analytics/metrics.py).
//
// The channel loop, draw order, clipping, histogram binning and percentile
// interpolation follow NumPy exactly. The random stream does not: NumPy's PCG64
// is replaced by a seeded mulberry32 generator (Beta = X/(X+Y) with
// Marsaglia-Tsang Gamma draws), so results agree with the Python sandbox
// statistically (Monte-Carlo noise), not bit-for-bit.
//
// No DOM APIs: runs in browsers and in Node >= 18.

export const CHANNELS = ['taiwan', 'china_export', 'hormuz', 'malacca'];
export const MAX_REVENUE_LOSS = { taiwan: 0.65, china_export: 0.30, hormuz: 0.12, malacca: 0.20 };
export const INPUT_COST_BETA = { taiwan: 0.10, china_export: 0.08, hormuz: 0.06, malacca: 0.04 };
export const DEFAULT_SCENARIO = { taiwan: 0.5, china_export: 0.5, hormuz: 0.2, malacca: 0.2, horizon: 2027 };
export const FAR_BANDS = [[85, 'AAA'], [75, 'AA'], [65, 'A'], [55, 'BBB'], [45, 'BB'], [35, 'B'], [0, 'CCC']];

const nowMs = () => (globalThis.performance && typeof globalThis.performance.now === 'function'
  ? globalThis.performance.now() : Date.now());

// ---------------------------------------------------------------------------
// Python / NumPy numeric helpers
// ---------------------------------------------------------------------------

/**
 * Python's round(x, ndigits) for floats: correctly rounded from the exact
 * binary value, ties (only possible for exactly representable halves) to even.
 */
export function pyRound(x, ndigits = 0) {
  if (typeof x !== 'number' || !Number.isFinite(x) || x === 0) return x;
  const ax = Math.abs(x);
  if (ax >= 1e21 || ndigits > 50) return x;
  const nd = Math.max(0, Math.trunc(ndigits));
  let s = ax.toFixed(nd); // exact value, ties away from zero
  const ext = ax.toFixed(Math.min(100, nd + 41));
  const dot = ext.indexOf('.');
  const tail = ext.slice(dot + 1 + nd);
  if (/^50*$/.test(tail)) { // exact tie -> banker's rounding
    const truncated = nd === 0 ? ext.slice(0, dot) : ext.slice(0, dot + 1 + nd);
    const last = Number(truncated[truncated.length - 1]);
    if (last % 2 === 0) s = truncated;
  }
  const r = Number(s);
  return x < 0 ? -r : r;
}

function pairwise(a, lo, n) {
  if (n < 8) {
    let res = -0.0;
    for (let i = 0; i < n; i++) res += a[lo + i];
    return res;
  }
  if (n <= 128) {
    let r0 = a[lo], r1 = a[lo + 1], r2 = a[lo + 2], r3 = a[lo + 3];
    let r4 = a[lo + 4], r5 = a[lo + 5], r6 = a[lo + 6], r7 = a[lo + 7];
    const stop = n - (n % 8);
    let i = 8;
    for (; i < stop; i += 8) {
      const p = lo + i;
      r0 += a[p]; r1 += a[p + 1]; r2 += a[p + 2]; r3 += a[p + 3];
      r4 += a[p + 4]; r5 += a[p + 5]; r6 += a[p + 6]; r7 += a[p + 7];
    }
    let res = ((r0 + r1) + (r2 + r3)) + ((r4 + r5) + (r6 + r7));
    for (; i < n; i++) res += a[lo + i];
    return res;
  }
  let n2 = Math.floor(n / 2);
  n2 -= n2 % 8;
  return pairwise(a, lo, n2) + pairwise(a, lo + n2, n - n2);
}

/** np.sum for a contiguous float64 vector (pairwise summation, identity 0). */
export function npSum(a) { return 0 + pairwise(a, 0, a.length); }

/** np.mean */
export function npMean(a) { return npSum(a) / a.length; }

/** np.std (population, ddof=0) */
export function npStd(a) {
  const n = a.length;
  const mean = npSum(a) / n;
  const sq = new Float64Array(n);
  for (let i = 0; i < n; i++) { const d = a[i] - mean; sq[i] = d * d; }
  return Math.sqrt(npSum(sq) / n);
}

/** np.percentile(x, q) with the default 'linear' method, on an already sorted array. */
export function npPercentileSorted(sorted, q) {
  const n = sorted.length;
  const vi = (n - 1) * (q / 100);
  if (vi >= n - 1) return sorted[n - 1];
  if (vi < 0) return sorted[0];
  const prev = Math.floor(vi);
  const gamma = vi - prev;
  const a = sorted[prev];
  const b = sorted[prev + 1];
  const diff = b - a;
  return gamma >= 0.5 ? b - diff * (1 - gamma) : a + diff * gamma;
}

/** np.histogram(x, bins) with uniform bins over [min, max] (edges via np.linspace). */
export function npHistogram(x, bins) {
  const n = x.length;
  let first = Infinity, last = -Infinity;
  for (let i = 0; i < n; i++) { const v = x[i]; if (v < first) first = v; if (v > last) last = v; }
  if (n === 0) { first = 0; last = 1; }
  if (first === last) { first -= 0.5; last += 0.5; }
  const edges = new Float64Array(bins + 1);
  const delta = last - first;
  const step = delta / bins;
  for (let i = 0; i <= bins; i++) edges[i] = (step === 0 ? (i / bins) * delta : i * step) + first;
  edges[bins] = last;
  const counts = new Float64Array(bins);
  const denom = last - first;
  for (let i = 0; i < n; i++) {
    const v = x[i];
    if (!(v >= first && v <= last)) continue;
    let idx = Math.trunc(((v - first) / denom) * bins);
    if (idx === bins) idx -= 1;
    if (v < edges[idx]) idx -= 1;
    if (v >= edges[idx + 1] && idx !== bins - 1) idx += 1;
    counts[idx] += 1;
  }
  return { edges: Array.from(edges), counts: Array.from(counts) };
}

// ---------------------------------------------------------------------------
// Random numbers
// ---------------------------------------------------------------------------

/** mulberry32: 32-bit seeded PRNG, uniform on [0, 1). */
export function mulberry32(seed) {
  let a = seed >>> 0;
  return function next() {
    a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Uniform / normal / gamma / beta generators driven by one mulberry32 stream. */
export function createRng(seed) {
  const uniform = mulberry32(seed);
  let spare = 0;
  let hasSpare = false;
  function normal() { // Marsaglia polar method
    if (hasSpare) { hasSpare = false; return spare; }
    let u, v, s;
    do {
      u = 2 * uniform() - 1;
      v = 2 * uniform() - 1;
      s = u * u + v * v;
    } while (s >= 1 || s === 0);
    const m = Math.sqrt(-2 * Math.log(s) / s);
    spare = v * m;
    hasSpare = true;
    return u * m;
  }
  function gamma(k) { // Marsaglia & Tsang (2000); k < 1 via the boost Gamma(k+1)·U^(1/k)
    if (k < 1) {
      const g = gamma(k + 1);
      return g * Math.pow(1 - uniform(), 1 / k);
    }
    const d = k - 1 / 3;
    const c = 1 / Math.sqrt(9 * d);
    for (;;) {
      let x, v;
      do { x = normal(); v = 1 + c * x; } while (v <= 0);
      v = v * v * v;
      const u = uniform();
      const x2 = x * x;
      if (u < 1 - 0.0331 * x2 * x2) return d * v;
      if (Math.log(u) < 0.5 * x2 + d * (1 - v + Math.log(v))) return d * v;
    }
  }
  function beta(a, b) {
    const x = gamma(a);
    const y = gamma(b);
    return x / (x + y);
  }
  return { uniform, normal, gamma, beta };
}

// ---------------------------------------------------------------------------
// Stress program replica
// ---------------------------------------------------------------------------

function num(v, fallback) {
  const x = Number(v);
  return Number.isFinite(x) ? x : fallback;
}

const clamp01 = x => Math.max(0, Math.min(1, x));

/**
 * Port of STRESS_PROGRAM. `inputs` is a snapshot's `analysis.stress.inputs`
 * (optionally with `__code`: the Python program text to pass through as `code`);
 * `scenario` = {taiwan, china_export, hormuz, malacca, horizon} with 0-1 severities.
 * Returns the StressResult JSON shape.
 */
export function runStressReplica(inputs, scenario = {}) {
  const t0 = nowMs();
  inputs = inputs || {};
  scenario = scenario || {};
  const sevIn = inputs.severity || {};
  const severity = {};
  for (const k of CHANNELS) severity[k] = clamp01(num(scenario[k], num(sevIn[k], DEFAULT_SCENARIO[k])));
  const horizon = Math.trunc(num(scenario.horizon, num(inputs.horizon, DEFAULT_SCENARIO.horizon)));
  const seed = Math.trunc(num(inputs.seed, 42));
  const paths = Math.max(1, Math.trunc(num(inputs.paths, 20000)));
  const baseYear = Math.trunc(num(inputs.base_year, horizon - 1));
  const exposureIn = inputs.exposure || {};
  const exposure = {};
  for (const k of CHANNELS) exposure[k] = num(exposureIn[k], 0);
  const maxRevenueLoss = { ...MAX_REVENUE_LOSS, ...(inputs.max_revenue_loss || {}) };
  const inputCostBeta = { ...INPUT_COST_BETA, ...(inputs.input_cost_beta || {}) };
  const baseRevenue = num(inputs.base_revenue, 0);
  const grossMargin = num(inputs.gross_margin, 0);
  const baseCogs = num(inputs.base_cogs, baseRevenue * (1 - grossMargin));
  let baseFcf = num(inputs.base_fcf, 0);
  if (baseFcf === 0) baseFcf = 0.01 * baseRevenue;

  const rng = createRng(seed);
  const years = Math.max(1, horizon - baseYear);
  const rev = new Float64Array(paths);
  const cost = new Float64Array(paths);
  const hit = new Uint8Array(paths);
  const depth = new Float64Array(paths);
  const duration = new Float64Array(paths);

  for (const k of CHANNELS) {
    const s = severity[k];
    const e = exposure[k];
    const pEvent = Math.min(0.95, s * (0.55 + 0.10 * years));
    // Same draw order as NumPy: hit block, depth block, duration block, cost block.
    for (let i = 0; i < paths; i++) hit[i] = rng.uniform() < pEvent ? 1 : 0;
    const depthScale = 0.5 + 0.5 * s;
    const mrl = maxRevenueLoss[k];
    for (let i = 0; i < paths; i++) depth[i] = rng.beta(2.0, 3.0) * mrl * e * depthScale;
    for (let i = 0; i < paths; i++) duration[i] = 0.25 + (1.0 - 0.25) * rng.uniform();
    for (let i = 0; i < paths; i++) rev[i] = rev[i] - hit[i] * depth[i] * duration[i];
    const icb = inputCostBeta[k];
    for (let i = 0; i < paths; i++) cost[i] = cost[i] + hit[i] * rng.beta(2.0, 5.0) * icb * e * s;
  }

  const absFcf = Math.abs(baseFcf);
  const fcfShock = new Float64Array(paths);
  const revPct = new Float64Array(paths);
  let ddCount = 0;
  for (let i = 0; i < paths; i++) {
    let r = rev[i];
    if (r < -0.95) r = -0.95; else if (r > 0.0) r = 0.0;
    rev[i] = r;
    revPct[i] = r * 100.0;
    const lostRevenue = -r * baseRevenue;
    const extraCost = cost[i] * baseCogs;
    const fcf = baseFcf - lostRevenue * grossMargin - extraCost;
    const fs = (fcf - baseFcf) / absFcf;
    fcfShock[i] = fs;
    if (fs < -0.20) ddCount++;
  }

  const hist = npHistogram(revPct, 24);
  const revMean = npMean(rev);
  const fcfMean = npMean(fcfShock);
  const revSorted = Float64Array.from(rev).sort();
  const fcfSorted = Float64Array.from(fcfShock).sort();
  const pct = (sorted, q) => npPercentileSorted(sorted, q);
  const r2 = v => pyRound(v, 2);

  const outInputs = {
    seed,
    paths,
    horizon,
    base_year: baseYear,
    severity,
    exposure,
    max_revenue_loss: maxRevenueLoss,
    input_cost_beta: inputCostBeta,
    base_revenue: baseRevenue,
    base_cogs: baseCogs,
    gross_margin: grossMargin,
    base_fcf: baseFcf,
    rng: 'mulberry32',
    engine: 'browser-replica',
  };
  const result = {
    paths,
    seed,
    base_revenue: baseRevenue,
    base_fcf: baseFcf,
    revenue_shock_pct: {
      p05: r2(pct(revSorted, 5) * 100), p50: r2(pct(revSorted, 50) * 100),
      p95: r2(pct(revSorted, 95) * 100), mean: r2(revMean * 100),
    },
    fcf_shock_pct: {
      p05: r2(pct(fcfSorted, 5) * 100), p50: r2(pct(fcfSorted, 50) * 100),
      p95: r2(pct(fcfSorted, 95) * 100), mean: r2(fcfMean * 100),
    },
    prob_fcf_drawdown_gt_20: pyRound(ddCount / paths, 4),
    histogram: { edges: hist.edges.map(v => pyRound(v, 3)), counts: hist.counts },
    code: typeof inputs.__code === 'string' ? inputs.__code : '',
    inputs: outInputs,
  };
  outInputs.sandbox_ms = pyRound(nowMs() - t0, 1);
  return result;
}

// ---------------------------------------------------------------------------
// Scenario-dependent metrics (SRS) and FAR re-derivation
// ---------------------------------------------------------------------------

const clip = (x, lo = 0, hi = 100) => Math.max(lo, Math.min(hi, x));

function cloneJson(x) {
  return typeof structuredClone === 'function' ? structuredClone(x) : JSON.parse(JSON.stringify(x));
}

function weighted(components) {
  let s = 0;
  for (const c of components) s += Number(c.value) * Number(c.weight);
  return s;
}

/** FAR letter band for an (unrounded) FAR score. */
export function farRating(score) {
  for (const [t, r] of FAR_BANDS) if (score >= t) return r;
  return 'CCC';
}

/**
 * Given the snapshot `metrics` (CPI, GPI, SRS, FAR) and a new StressResult,
 * return a new metrics array: CPI and GPI unchanged, SRS components 1-2
 * recomputed from the stress result (component 3, asset intensity, reused),
 * FAR components 1-2 re-derived from GPI / CPI. Formulas: metrics.py.
 */
export function recomputeScenarioMetrics(metrics, stress) {
  const out = cloneJson(metrics || []);
  const byCode = {};
  for (const m of out) byCode[m.code] = m;

  const srs = byCode.SRS;
  if (srs && stress) {
    const comps = srs.components || [];
    const pDd = Number(stress.prob_fcf_drawdown_gt_20) * 100.0;
    const p05 = Number((stress.revenue_shock_pct || {}).p05 ?? 0);
    const revTail = clip(Math.abs(p05) / 30.0 * 100.0);
    if (comps[0]) comps[0].value = pyRound(pDd, 1);
    if (comps[1]) comps[1].value = pyRound(revTail, 1);
    srs.value = pyRound(clip(weighted(comps)), 1);
  }

  const far = byCode.FAR;
  if (far) {
    const comps = far.components || [];
    // The stored FAR components were rounded from the unrounded GPI / CPI; keep
    // them when they agree with the published (1-dp) GPI / CPI within rounding,
    // otherwise re-derive from the published values.
    const derive = (comp, metric) => {
      if (!comp || !metric) return;
      const fresh = pyRound(100 - Number(metric.value), 1);
      if (!(Math.abs(fresh - Number(comp.value)) <= 0.1 + 1e-9)) comp.value = fresh;
    };
    derive(comps[0], byCode.GPI);
    derive(comps[1], byCode.CPI);
    const score = clip(weighted(comps));
    far.value = pyRound(score, 1);
    far.rating = farRating(score);
  }
  return out;
}
