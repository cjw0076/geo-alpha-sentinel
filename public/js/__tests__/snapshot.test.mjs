// Snapshot-mode tests (Node >= 18, no dependencies).
//   node public/js/__tests__/snapshot.test.mjs
// Python parity checks call /usr/bin/python3 (override with SENTINEL_PYTHON).
// The PortWatch test needs network access; it is skipped with a notice when offline.

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import zlib from 'node:zlib';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

import { runStressReplica, recomputeScenarioMetrics, pyRound, farRating } from '../stress_replica.js';
import {
  createSnapshotApi, normalizeTokens, sequenceRatio, buildSanctionsIndex, screenSanctions,
  fetchPortWatchRaw, chokepointsFromRaw, replayDelays,
} from '../snapshot.js';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PUBLIC = path.resolve(HERE, '..', '..');
const SNAP = path.join(PUBLIC, 'snapshots');
const ROOT = path.resolve(PUBLIC, '..');
const PYTHON = process.env.SENTINEL_PYTHON || '/usr/bin/python3';

// ---------------------------------------------------------------------------
// tiny harness
// ---------------------------------------------------------------------------
const tests = [];
const test = (name, fn) => tests.push({ name, fn });
class Skip extends Error {}
const skip = msg => { throw new Skip(msg); };
function assert(cond, msg) { if (!cond) throw new Error(`assertion failed: ${msg}`); }
function assertClose(a, b, tol, msg) {
  assert(Number.isFinite(a) && Number.isFinite(b) && Math.abs(a - b) <= tol + 1e-12, `${msg}: |${a} - ${b}| > ${tol}`);
}
function diffPath(a, b, p = '$') { // first structural/value difference, or null
  if (typeof a === 'number' && typeof b === 'number') return a === b || (Number.isNaN(a) && Number.isNaN(b)) ? null : `${p}: ${a} !== ${b}`;
  if (a === null || b === null || typeof a !== 'object' || typeof b !== 'object') return a === b ? null : `${p}: ${JSON.stringify(a)} !== ${JSON.stringify(b)}`;
  if (Array.isArray(a) !== Array.isArray(b)) return `${p}: array vs object`;
  const ka = Object.keys(a), kb = Object.keys(b);
  if (ka.length !== kb.length || ka.some(k => !(k in b))) return `${p}: keys [${ka}] vs [${kb}]`;
  for (const k of ka) { const d = diffPath(a[k], b[k], `${p}.${k}`); if (d) return d; }
  return null;
}
const readJson = rel => JSON.parse(fs.readFileSync(path.join(SNAP, rel), 'utf8'));
const notes = [];

function python(code, { env = {}, timeout = 180000, input } = {}) {
  const r = spawnSync(PYTHON, ['-c', code], {
    cwd: ROOT, env: { ...process.env, ...env }, encoding: 'utf8', timeout, input, maxBuffer: 256 * 1024 * 1024,
  });
  if (r.error) throw r.error;
  if (r.status !== 0) throw new Error(`python exited ${r.status}: ${(r.stderr || '').slice(-800)}`);
  return JSON.parse(r.stdout);
}

// Disk-backed fetch: './snapshots/<path>' -> public/snapshots/<path>; http(s) -> network.
function diskFetch({ csl = 'gzip' } = {}) {
  return async (url, init) => {
    const u = String(url);
    if (/^https?:/.test(u)) return globalThis.fetch(u, init);
    const rel = u.replace(/^\.?\/?snapshots\//, '');
    const file = path.join(SNAP, rel);
    if (!file.startsWith(SNAP) || !fs.existsSync(file)) return new Response('not found', { status: 404 });
    let body = fs.readFileSync(file);
    if (csl === 'plain' && rel.endsWith('.gz')) body = zlib.gunzipSync(body); // server already decoded it
    return new Response(body, { status: 200 });
  };
}

const index = readJson('index.json');
const SCEN = index.scenario;

// ---------------------------------------------------------------------------
// 1. stress replica
// ---------------------------------------------------------------------------
const timing = {};
test('replica: 20,000-path timing (first call in process + warm median)', () => {
  const inputs = readJson('NVDA/analysis.json').stress.inputs;
  assert(inputs.paths === 20000, 'snapshot uses 20,000 paths');
  let t0 = performance.now();
  runStressReplica(inputs, SCEN);
  timing.first = performance.now() - t0;
  const runs = [];
  for (let i = 0; i < 7; i++) { t0 = performance.now(); runStressReplica(inputs, SCEN); runs.push(performance.now() - t0); }
  runs.sort((a, b) => a - b);
  timing.median = runs[3];
  assert(timing.first < 150, `first call ${timing.first.toFixed(1)} ms < 150 ms`);
  assert(timing.median < 150, `median ${timing.median.toFixed(1)} ms < 150 ms`);
});

const parityRows = [];
for (const T of ['NVDA', 'TSM', 'TSLA']) {
  test(`replica vs Python sandbox: ${T}`, () => {
    const a = readJson(`${T}/analysis.json`);
    const py = a.stress;
    const js = runStressReplica({ ...py.inputs, __code: py.code }, SCEN);
    // shape
    assert(diffPath(Object.keys(js).sort(), Object.keys(py).sort()) === null, 'StressResult keys');
    for (const k of ['revenue_shock_pct', 'fcf_shock_pct']) assert(diffPath(Object.keys(js[k]).sort(), Object.keys(py[k]).sort()) === null, `${k} keys`);
    assert(js.histogram.edges.length === 25 && js.histogram.counts.length === 24, 'histogram 24 bins / 25 edges');
    assert(js.histogram.counts.reduce((s, c) => s + c, 0) === js.paths, 'histogram counts sum to paths');
    assert(js.code === py.code, 'code passed through');
    assert(js.paths === py.paths && js.seed === py.seed && js.base_revenue === py.base_revenue && js.base_fcf === py.base_fcf, 'paths/seed/base');
    // numbers
    const jsM = recomputeScenarioMetrics(a.metrics, js);
    const srs = ms => ms.find(m => m.code === 'SRS').value;
    const row = {
      ticker: T,
      p50: [js.revenue_shock_pct.p50, py.revenue_shock_pct.p50],
      p05: [js.revenue_shock_pct.p05, py.revenue_shock_pct.p05],
      pdd: [pyRound(js.prob_fcf_drawdown_gt_20 * 100, 2), pyRound(py.prob_fcf_drawdown_gt_20 * 100, 2)],
      srs: [srs(jsM), srs(a.metrics)],
    };
    parityRows.push(row);
    assertClose(row.p50[0], row.p50[1], 1.5, 'revenue p50 (pp)');
    assertClose(row.p05[0], row.p05[1], 1.5, 'revenue p05 (pp)');
    assertClose(row.pdd[0], row.pdd[1], 1.5, 'P(FCF drawdown>20%) x100');
    assertClose(row.srs[0], row.srs[1], 2.0, 'SRS');
    // determinism
    assert(diffPath(runStressReplica(py.inputs, SCEN).revenue_shock_pct, js.revenue_shock_pct) === null, 'seeded => deterministic');
  });
}

test('replica: monotonic in Taiwan severity (TSM, 0.2 -> 1.0)', () => {
  const inputs = readJson('TSM/analysis.json').stress.inputs;
  const lo = runStressReplica(inputs, { ...SCEN, taiwan: 0.2 });
  const hi = runStressReplica(inputs, { ...SCEN, taiwan: 1.0 });
  notes.push(`TSM mean revenue shock: taiwan=0.2 -> ${lo.revenue_shock_pct.mean}%, taiwan=1.0 -> ${hi.revenue_shock_pct.mean}%`);
  assert(hi.revenue_shock_pct.mean < lo.revenue_shock_pct.mean, 'mean revenue shock more negative');
  assert(hi.inputs.severity.taiwan === 1 && lo.inputs.severity.taiwan === 0.2, 'severity recorded in inputs');
});

test('metrics: recompute(snapshot metrics, Python stress) reproduces SRS/FAR for every snapshot', () => {
  let n = 0;
  for (const it of index.items) {
    const a = readJson(`${it.ticker}/analysis.json`);
    const re = recomputeScenarioMetrics(a.metrics, a.stress);
    const d = diffPath(re, a.metrics);
    assert(d === null, `${it.ticker} ${d}`);
    n++;
  }
  assert(n === index.items.length && n > 0, 'all snapshots checked');
  assert(farRating(85) === 'AAA' && farRating(84.99) === 'AA' && farRating(55) === 'BBB' && farRating(34.9) === 'CCC', 'FAR bands');
});

test('pyRound matches Python round() (incl. exact ties -> even)', () => {
  const py = python('import json; print(json.dumps([round(0.125,2), round(2.675,2), round(0.375,2), round(-0.125,2), round(2.5), round(3.5), round(-17.284999,2), round(1.0005,3), round(0.8875,3), round(13.08,1), round(57.6,1)]))');
  const js = [pyRound(0.125, 2), pyRound(2.675, 2), pyRound(0.375, 2), pyRound(-0.125, 2), pyRound(2.5, 0), pyRound(3.5, 0), pyRound(-17.284999, 2), pyRound(1.0005, 3), pyRound(0.8875, 3), pyRound(13.08, 1), pyRound(57.6, 1)];
  assert(diffPath(js, py) === null, `${JSON.stringify(js)} vs ${JSON.stringify(py)}`);
});

// ---------------------------------------------------------------------------
// 2. snapshot API over files
// ---------------------------------------------------------------------------
const api = createSnapshotApi({ fetchImpl: diskFetch() });

test('api: health / index / search', async () => {
  const h = await api.health();
  assert(h.status === 'snapshot' && typeof h.version === 'string' && h.llm.enabled === false && h.llm.model === null, 'health shape');
  assert(h.generated_at === index.generated_at, 'generated_at from index.json');
  const idx = await api.index();
  assert(idx.items.length === index.items.length, 'index items');
  const s = await api.search('nv');
  assert(s[0].ticker === 'NVDA' && 'cik' in s[0] && s[0].name, 'search prefix NVDA first');
  assert((await api.search('')).length === 0, 'empty query');
  assert((await api.search('semiconductor')).some(r => r.ticker === 'TSM'), 'name substring');
  assert((await api.search('a')).length <= 12, 'max 12');
});

test('api: company / exposure / analysis, aliases, NOT_IN_SNAPSHOT', async () => {
  const c = await api.company('nvda');
  assert(c.ticker === 'NVDA' && c.cik && c.fundamentals, 'company shape');
  assert((await api.search('NVDA'))[0].cik === c.cik, 'search reuses known cik');
  const e = await api.exposure('TSM');
  assert(e.ticker === 'TSM' && e.categories, 'exposure shape');
  const a = await api.analysis('TSMC');
  assert(a.ticker === 'TSM' && Array.isArray(a.events) && a.stress && a.metrics.length === 4, 'alias TSMC -> TSM');
  a.metrics.length = 0; // mutation must not leak into the cache
  assert((await api.analysis('TSM')).metrics.length === 4, 'cached objects are cloned');
  let err = null;
  try { await api.company('ZZZZ'); } catch (x) { err = x; }
  assert(err && err.code === 'NOT_IN_SNAPSHOT', 'unknown ticker code');
  assert(Array.isArray(err.coverage) && err.coverage.length === index.items.length && err.coverage.includes('NVDA'), 'coverage list');
});

test('api: minerals / macro / chokepoints snapshot', async () => {
  const m = await api.minerals();
  assert(m.edition && m.items.length > 0, 'minerals');
  const mc = await api.macro();
  assert(mc.items.length > 0, 'macro');
  const ch = await api.chokepoints({ live: false });
  assert(ch.source === 'snapshot' && ch.items.length === 28 && ch.as_of, 'chokepoints snapshot');
});

test('api: stress(ticker, scenario) replica + exact-at-snapshot option', async () => {
  const a = readJson('TSM/analysis.json');
  const r = await api.stress('TSM', { taiwan: 1.0, china_export: 0.5, hormuz: 0.2, malacca: 0.2, horizon: 2028 });
  assert(r.mode === 'replica', 'mode');
  assert(r.stress.code === a.stress.code && r.stress.code.length > 100, 'Python program text passed through');
  assert(r.stress.inputs.severity.taiwan === 1 && r.stress.inputs.horizon === 2028, 'scenario applied');
  assert(!('__code' in r.stress.inputs), 'no __code leak');
  assert(diffPath(r.exposure_by_channel, a.engine.exposure_by_channel) === null, 'exposure_by_channel');
  assert(r.metrics.length === 4, 'metrics');
  for (const code of ['CPI', 'GPI']) assert(diffPath(r.metrics.find(m => m.code === code), a.metrics.find(m => m.code === code)) === null, `${code} unchanged`);
  const srs = r.metrics.find(m => m.code === 'SRS');
  assert(srs.value > a.metrics.find(m => m.code === 'SRS').value, 'harsher scenario raises SRS');
  const exact = await api.stress('TSM', SCEN, { exactAtSnapshotScenario: true });
  assert(exact.mode === 'snapshot' && diffPath(exact.stress, a.stress) === null && diffPath(exact.metrics, a.metrics) === null, 'exact snapshot numbers on request');
  const partial = await api.stress('TSM', { taiwan: 0.9 });
  assert(partial.stress.inputs.severity.hormuz === SCEN.hormuz && partial.stress.inputs.horizon === SCEN.horizon, 'missing fields default to the snapshot scenario');
});

test('api: analyzeReplay order, timing caps, result, cancel', async () => {
  const a = readJson('NVDA/analysis.json');
  const d = replayDelays(a.events, 1);
  assert(d.every(x => x <= 400) && d.reduce((s, x) => s + x, 0) <= 12000, 'gaps <= 400 ms, total <= 12 s');
  const many = Array.from({ length: 100 }, (_, i) => ({ t_ms: i * 1000 }));
  assert(Math.abs(replayDelays(many, 1).reduce((s, x) => s + x, 0) - 12000) < 1e-6, 'long runs squeezed to 12 s');
  const seen = [];
  let result = null;
  const cancel = api.analyzeReplay('NVDA', { speed: 1000, onEvent: e => seen.push(e), onResult: r => { result = r; } });
  assert(typeof cancel === 'function', 'returns cancel function');
  assert(await cancel.done === 'done', 'finished');
  assert(seen.length === a.events.length && seen.every((e, i) => e.seq === a.events[i].seq), 'all events in order');
  assert(result && result.run_id === a.run_id && result.memo && result.metrics.length === 4, 'AnalysisResult delivered');
  const seen2 = [];
  let result2 = null;
  const c2 = api.analyzeReplay('NVDA', { speed: 1, onEvent: e => { seen2.push(e); if (seen2.length === 3) c2(); }, onResult: r => { result2 = r; } });
  assert(await c2.done === 'cancelled', 'cancel resolves done');
  await new Promise(r => setTimeout(r, 500));
  assert(seen2.length === 3 && result2 === null, 'no callbacks after cancel');
  let err = null;
  const c3 = api.analyzeReplay('ZZZZ', { onError: e => { err = e; } });
  assert(await c3.done === 'error' && err.code === 'NOT_IN_SNAPSHOT', 'error path');
});

// ---------------------------------------------------------------------------
// 3. sanctions
// ---------------------------------------------------------------------------
const PARITY_QUERIES = ['Huawei Technologies', 'Semiconductor Manufacturing International Corporation', 'Rosoboronexport', 'Hikvision', 'Iran Aircraft Manufacturing Industries'];
let pySanc = null;
function pythonSanctions() {
  if (!pySanc) {
    pySanc = python(`
import json, sys
from sentinel.sources.sanctions import screen, _get_index
qs = json.loads(sys.stdin.read())
idx = _get_index()
norms = [[sorted(m['tok_set']), m['norm_str'], [[sorted(a[0]), a[1]] for a in m['alt_norms']]] for m in idx.entity_norms]
out = {'norms': norms, 'screens': {}}
for q in qs:
    top = screen(q).model_dump()
    full = screen(q, limit=100000).model_dump()
    out['screens'][q] = {'top': top, 'all': [[m['name'], m['matched_name'], m['score']] for m in full['matches']]}
print(json.dumps(out))
`, { input: JSON.stringify([...PARITY_QUERIES, 'NVIDIA']) });
  }
  return pySanc;
}

test('sanctions: functional (Huawei, NVIDIA, SMIC)', async () => {
  const h = await api.sanctions('Huawei Technologies');
  assert(h.matches.length >= 1, 'Huawei matches');
  assert(h.query === 'Huawei Technologies' && h.list_size > 10000 && h.snapshot_date && h.provenance.source === 'trade_csl', 'SanctionsScreen shape');
  const keys = ['name', 'matched_name', 'score', 'list_source', 'programs', 'country', 'entity_number', 'source_list_url'];
  assert(diffPath(Object.keys(h.matches[0]), keys) === null, 'SanctionsMatch keys');
  assert((await api.sanctions('NVIDIA')).matches.length === 0, 'NVIDIA clean');
  const smic = await api.sanctions('Semiconductor Manufacturing International Corporation');
  assert(smic.matches.some(m => /SEMICONDUCTOR MANUFACTURING INTERNATIONAL/i.test(m.name)), 'SMIC listed');
  let err = null;
  try { await api.sanctions('x'); } catch (x) { err = x; }
  assert(err && err.code === 'INVALID_QUERY', 'min length 2');
});

test('sanctions: pre-decompressed .gz response is accepted', async () => {
  const plain = createSnapshotApi({ fetchImpl: diskFetch({ csl: 'plain' }) });
  assert((await plain.sanctions('Huawei Technologies')).matches.length >= 1, 'plain JSON path');
});

test('sanctions: normalisation parity with Python over the whole CSL list', () => {
  const py = pythonSanctions().norms;
  const data = JSON.parse(zlib.gunzipSync(fs.readFileSync(path.join(SNAP, 'csl_entities.json.gz'))).toString('utf8'));
  const idx = buildSanctionsIndex(data);
  assert(py.length === idx.norms.length, 'entity count');
  let strings = 0;
  for (let i = 0; i < py.length; i++) {
    const m = idx.norms[i];
    const js = [[...m.tokSet].sort(), m.normStr, m.altNorms.map(([s, n]) => [[...s].sort(), n])];
    const pyRow = [py[i][0].slice().sort(), py[i][1], py[i][2].map(([s, n]) => [s.slice().sort(), n])];
    const d = diffPath(js, pyRow);
    assert(d === null, `entity ${i} (${data.entities[i].n}): ${d}`);
    strings += 1 + m.altNorms.length;
  }
  notes.push(`normalisation identical to Python for ${strings} CSL names (${py.length} entities)`);
});

test('sanctions: SequenceMatcher ratio parity with difflib (incl. autojunk >= 200 chars)', () => {
  const pairs = [
    ['huawei', 'huawei technologies'], ['rosoboronexport', 'rosoboroneksport oao'], ['abcd', 'bcda'], ['', ''], ['a', ''],
    ['private ltd', 'private shipping'], ['semiconductor manufacturing international', 'beijing international manufacturing semiconductor'],
    ['xi an aerospace', 'xi’an aerospace huaxun'], ['a'.repeat(5) + 'b', ('ab '.repeat(80)).trim()],
    ['the quick brown fox jumps over the lazy dog '.repeat(6), 'quick brown dog jumps over the lazy fox '.repeat(7)],
  ];
  const py = python(`
import json, sys, difflib
pairs = json.loads(sys.stdin.read())
print(json.dumps([difflib.SequenceMatcher(None, a, b).ratio() for a, b in pairs]))
`, { input: JSON.stringify(pairs) });
  const js = pairs.map(([a, b]) => sequenceRatio(a, b));
  assert(diffPath(js, py) === null, `${JSON.stringify(js)} vs ${JSON.stringify(py)}`);
});

const sancRows = [];
test('sanctions: screen parity with Python sentinel.sources.sanctions.screen (5 queries)', async () => {
  const py = pythonSanctions().screens;
  for (const q of [...PARITY_QUERIES, 'NVIDIA']) {
    const js = await api.sanctions(q);
    const p = py[q];
    const jsTop = js.matches[0] || null, pyTop = p.top.matches[0] || null;
    sancRows.push({ q, n: [js.matches.length, p.top.matches.length], top: [jsTop?.name ?? '-', pyTop?.name ?? '-'], score: [jsTop?.score ?? null, pyTop?.score ?? null] });
    assert(js.matches.length === p.top.matches.length, `${q}: match count ${js.matches.length} vs ${p.top.matches.length}`);
    if (!pyTop) continue;
    assertClose(jsTop.score, pyTop.score, 0.02, `${q}: top score`);
    // Python's order among equal scores follows set-iteration order (hash-seeded),
    // so the top name is compared tie-aware: it must be one of Python's matches with that score.
    const tied = p.all.filter(([, , s]) => s === pyTop.score).map(([n]) => n);
    assert(tied.includes(jsTop.name), `${q}: top name "${jsTop.name}" not among Python's top-score matches`);
    assert(diffPath(js.matches.map(m => m.score), p.top.matches.map(m => m.score)) === null, `${q}: score list`);
    const all = new Set(p.all.map(r => JSON.stringify(r)));
    for (const m of js.matches) assert(all.has(JSON.stringify([m.name, m.matched_name, m.score])), `${q}: ${m.name} / ${m.matched_name} / ${m.score} not produced by Python`);
    // full candidate list (no limit) must be identical as a set
    const jsAll = screenSanctions(buildSanctionsIndex(JSON.parse(zlib.gunzipSync(fs.readFileSync(path.join(SNAP, 'csl_entities.json.gz'))).toString('utf8'))), q, 100000);
    assert(jsAll.matches.length === p.all.length, `${q}: full match count ${jsAll.matches.length} vs ${p.all.length}`);
  }
});

// ---------------------------------------------------------------------------
// 4. PortWatch from the browser path
// ---------------------------------------------------------------------------
const pwInfo = {};
test('portwatch: live query (global fetch) vs Python get_chokepoints()', async () => {
  try {
    const probe = await fetch(`https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/PortWatch_chokepoints_database/FeatureServer/0/query?where=1%3D1&returnCountOnly=true&f=json`, { signal: AbortSignal.timeout(15000) });
    if (!probe.ok) skip(`PortWatch probe HTTP ${probe.status}`);
  } catch (e) {
    if (e instanceof Skip) throw e;
    skip(`offline (${e.cause?.code || e.name})`);
  }
  // (a) the API path as the UI uses it, with the 8 s budget (late data via onLive)
  const live = createSnapshotApi({ fetchImpl: diskFetch() });
  let t0 = performance.now();
  let lateResolve;
  const late = new Promise(resolve => { lateResolve = resolve; });
  const ch = await live.chokepoints({ onLive: v => { pwInfo.lateMs = performance.now() - t0; lateResolve(v); } });
  pwInfo.apiMs = performance.now() - t0;
  pwInfo.apiSource = ch.source;
  pwInfo.liveError = ch.live_error;
  assert(ch.items.length === 28 && ch.as_of, 'API chokepoints payload');
  if (ch.source === 'snapshot') {
    assert(ch.live_pending === true && pwInfo.apiMs >= 7900, `fell back without the 8 s timeout: ${ch.live_error}`);
    const v = await Promise.race([late, new Promise(resolve => setTimeout(() => resolve(null), 50000))]);
    assert(v && v.source === 'live' && v.items.length === 28, 'late live payload delivered via onLive');
    const again = await live.chokepoints();
    assert(again.source === 'live', 'next call served from the live cache');
  }
  // (b) raw pull for exact parity (no time budget)
  t0 = performance.now();
  const raw = await fetchPortWatchRaw();
  pwInfo.rawMs = performance.now() - t0;
  pwInfo.pages = raw.pages.length;
  const js = chokepointsFromRaw(raw);
  assert(js.items.length === 28, `28 chokepoints (got ${js.items.length})`);
  const jsH = js.items.find(c => c.id === 'chokepoint6');
  assert(jsH && jsH.name === 'Strait of Hormuz' && Number.isFinite(jsH.delta_pct) && jsH.series.length === 120, 'Hormuz present');
  pwInfo.jsDelta = jsH.delta_pct;

  // Exact parity: Python's get_chokepoints() run on the very same raw responses.
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'gas-pw-'));
  try {
    const rawFile = path.join(tmp, 'raw.json');
    fs.writeFileSync(rawFile, JSON.stringify(raw));
    const same = python(`
import json, sys
from sentinel.sources import portwatch as pw
raw = json.load(open(sys.argv[1] if len(sys.argv) > 1 else ${JSON.stringify(rawFile)}))
pages = {p['offset']: p['data'] for p in raw['pages']}
def fake(url, params=None, ttl=0, headers=None):
    if url == pw.CHOKEPOINT_META_URL:
        return raw['meta'], raw['retrieved_at']
    return pages.get(params['resultOffset'], {'features': []}), raw['retrieved_at']
pw.fetch_json = fake
as_of, items = pw.get_chokepoints()
print(json.dumps({'as_of': as_of, 'items': [c.model_dump() for c in items]}))
`);
    const d = diffPath(js, same);
    assert(d === null, `computation differs from Python on identical data: ${d}`);
    pwInfo.exact = true;

    // Independent Python live fetch (fresh cache dir), as the server would do it.
    t0 = performance.now();
    let pyLive = null;
    try {
      pyLive = python(`
import json
from sentinel.sources.portwatch import get_chokepoints
as_of, items = get_chokepoints()
print(json.dumps({'as_of': as_of, 'n': len(items), 'hormuz': [c.delta_pct for c in items if c.id == 'chokepoint6'][0]}))
`, { env: { SENTINEL_CACHE_DIR: path.join(tmp, 'cache') }, timeout: 240000 });
    } catch (e) {
      notes.push(`independent Python PortWatch fetch failed (${String(e.message).slice(0, 120)}); exact same-data parity above still holds`);
    }
    pwInfo.pyMs = performance.now() - t0;
    if (pyLive) {
      pwInfo.pyDelta = pyLive.hormuz;
      assert(pyLive.n === 28, 'Python 28 items');
      assertClose(jsH.delta_pct, pyLive.hormuz, 0.5, 'Hormuz delta_pct vs Python live');
    }
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

// ---------------------------------------------------------------------------
// run
// ---------------------------------------------------------------------------
let pass = 0, fail = 0, skipped = 0;
for (const t of tests) {
  const t0 = performance.now();
  try {
    await t.fn();
    pass++;
    console.log(`PASS  ${t.name}  (${(performance.now() - t0).toFixed(0)} ms)`);
  } catch (e) {
    if (e instanceof Skip) { skipped++; console.log(`SKIP  ${t.name}: ${e.message}`); continue; }
    fail++;
    const cause = e && e.cause ? ` [cause: ${e.cause.code || e.cause.message || e.cause}]` : '';
    console.log(`FAIL  ${t.name}${cause}\n      ${e && e.stack ? e.stack.split('\n').slice(0, 3).join('\n      ') : e}`);
  }
}

const pad = (s, n) => String(s).padStart(n);
console.log('\nReplica (browser, mulberry32) vs Python sandbox (NumPy PCG64), snapshot scenario', JSON.stringify(SCEN));
console.log('ticker | rev p50 % js / py (Δ)      | rev p05 % js / py (Δ)        | P(FCF dd>20%) ×100 js / py (Δ) | SRS js / py (Δ)');
for (const r of parityRows) {
  const f = ([a, b]) => `${pad(a.toFixed(2), 7)} / ${pad(b.toFixed(2), 7)} (${(a - b >= 0 ? '+' : '') + (a - b).toFixed(2)})`;
  console.log(`${r.ticker.padEnd(6)} | ${f(r.p50)} | ${f(r.p05)} | ${f(r.pdd)}   | ${f(r.srs)}`);
}
if (timing.first !== undefined) console.log(`20,000 paths: first call ${timing.first.toFixed(1)} ms, warm median ${timing.median.toFixed(1)} ms`);
if (sancRows.length) {
  console.log('\nSanctions screen js / py');
  for (const r of sancRows) console.log(`  ${r.q.padEnd(56)} n=${r.n[0]}/${r.n[1]}  top=${r.score[0] ?? '-'}/${r.score[1] ?? '-'}  ${r.top[0] === r.top[1] ? r.top[0] : `${r.top[0]}  |  ${r.top[1]}`}`);
}
if (pwInfo.pages !== undefined) {
  console.log(`\nPortWatch live: API path ${pwInfo.apiMs.toFixed(0)} ms (source=${pwInfo.apiSource}${pwInfo.liveError ? `, ${pwInfo.liveError}; live landed via onLive at ${pwInfo.lateMs?.toFixed(0)} ms` : ''}); raw pull ${pwInfo.rawMs.toFixed(0)} ms, ${pwInfo.pages} pages; same-data parity with Python: ${pwInfo.exact ? 'exact' : 'n/a'}; Hormuz delta_pct js ${pwInfo.jsDelta} / py-live ${pwInfo.pyDelta ?? 'n/a'} (${pwInfo.pyMs ? `${(pwInfo.pyMs / 1000).toFixed(1)} s` : ''})`);
}
for (const n of notes) console.log(`note: ${n}`);
console.log(`\n${pass} passed, ${fail} failed, ${skipped} skipped`);
process.exit(fail ? 1 : 0);
