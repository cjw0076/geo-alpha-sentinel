// Snapshot mode: a browser-side stand-in for the Geo-Alpha Sentinel HTTP API
// (docs/API.md) for static hosting (GitHub Pages). Every method returns the same
// JSON shape as the live endpoint, built from public/snapshots/ (written by
// etl/build_snapshots.py), from IMF PortWatch queried directly from the browser,
// and from browser ports of the Python sanctions screen and stress program.
//
// No DOM APIs: runs in browsers and in Node >= 18 (pass `fetchImpl` for files).

import {
  DEFAULT_SCENARIO, CHANNELS, npMean, npStd, pyRound, recomputeScenarioMetrics, runStressReplica,
} from './stress_replica.js';

export const SNAPSHOT_API_VERSION = '2.0.0';
const TICKER_ALIASES = { TSMC: 'TSM', '2330.TW': 'TSM', SAMSUNG: 'SSNLF', GOOG: 'GOOGL', 'BRK.B': 'BRK-B' };

const clone = x => (x === undefined ? x : (typeof structuredClone === 'function'
  ? structuredClone(x) : JSON.parse(JSON.stringify(x))));

/** ISO-8601 UTC without milliseconds, like sentinel.http.utcnow_iso(). */
export function utcnowIso(d = new Date()) {
  return d.toISOString().replace(/\.\d{3}Z$/, 'Z');
}

function codedError(message, code, extra = {}) {
  const err = new Error(message);
  err.code = code;
  Object.assign(err, extra);
  return err;
}

// ---------------------------------------------------------------------------
// Sanctions screening (port of sentinel/sources/sanctions.py)
// ---------------------------------------------------------------------------

export const CSL_URL = 'https://data.trade.gov/downloadable_consolidated_screening_list/v1/consolidated.csv';
export const CORPORATE_SUFFIXES = new Set([
  'co', 'ltd', 'inc', 'corp', 'corporation', 'company', 'limited', 'llc', 'group',
  'holdings', 'technologies', 'technology', 'the',
]);
// Python str.isspace() set (what re's \s and str.split() use).
const PY_WS = '\\t\\n\\v\\f\\r\\x1c-\\x20\\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000';
// Python re `[^\w\s]` (Unicode): \w = letters, numbers, underscore.
const NON_WORD_RE = new RegExp(`[^\\p{L}\\p{N}_${PY_WS}]`, 'gu');
const WS_SPLIT_RE = new RegExp(`[${PY_WS}]+`, 'u');
const SURROGATE_RE = /[\uD800-\uDFFF]/;

const cpLength = s => (SURROGATE_RE.test(s) ? Array.from(s).length : s.length);

function cpCompare(a, b) { // Python str ordering (by code point)
  const A = Array.from(a), B = Array.from(b);
  const n = Math.min(A.length, B.length);
  for (let i = 0; i < n; i++) {
    const d = A[i].codePointAt(0) - B[i].codePointAt(0);
    if (d) return d;
  }
  return A.length - B.length;
}

/** normalize_tokens(): returns [Set of tokens, sorted token string]. */
export function normalizeTokens(text) {
  const cleaned = String(text ?? '').toLowerCase().replace(NON_WORD_RE, ' ');
  const parts = cleaned.split(WS_SPLIT_RE).filter(w => w.length > 0);
  let words = parts.filter(w => !CORPORATE_SUFFIXES.has(w) && cpLength(w) > 1);
  if (!words.length) words = parts;
  const sorted = words.slice();
  if (sorted.some(w => SURROGATE_RE.test(w))) sorted.sort(cpCompare);
  else sorted.sort();
  return [new Set(words), sorted.join(' ')];
}

const toSeq = s => (SURROGATE_RE.test(s) ? Array.from(s) : s);

function findLongestMatch(a, b, b2j, alo, ahi, blo, bhi) {
  let besti = alo, bestj = blo, bestsize = 0;
  let j2len = new Map();
  for (let i = alo; i < ahi; i++) {
    const newj2len = new Map();
    const js = b2j.get(a[i]);
    if (js) {
      for (let x = 0; x < js.length; x++) {
        const j = js[x];
        if (j < blo) continue;
        if (j >= bhi) break;
        const k = (j2len.get(j - 1) || 0) + 1;
        newj2len.set(j, k);
        if (k > bestsize) { besti = i - k + 1; bestj = j - k + 1; bestsize = k; }
      }
    }
    j2len = newj2len;
  }
  // isjunk is None, so bjunk is empty: extend with equal (possibly "popular") elements.
  while (besti > alo && bestj > blo && a[besti - 1] === b[bestj - 1]) {
    besti--; bestj--; bestsize++;
  }
  while (besti + bestsize < ahi && bestj + bestsize < bhi && a[besti + bestsize] === b[bestj + bestsize]) {
    bestsize++;
  }
  return [besti, bestj, bestsize];
}

/** difflib.SequenceMatcher(None, a, b).ratio() — Ratcliff/Obershelp with autojunk. */
export function sequenceRatio(aStr, bStr) {
  const a = toSeq(aStr), b = toSeq(bStr);
  const la = a.length, lb = b.length;
  if (la + lb === 0) return 1.0;
  const b2j = new Map();
  for (let i = 0; i < lb; i++) {
    const e = b[i];
    const l = b2j.get(e);
    if (l) l.push(i); else b2j.set(e, [i]);
  }
  if (lb >= 200) { // autojunk: purge "popular" elements
    const ntest = Math.floor(lb / 100) + 1;
    for (const [e, idxs] of b2j) if (idxs.length > ntest) b2j.delete(e);
  }
  let matches = 0;
  const queue = [[0, la, 0, lb]];
  while (queue.length) {
    const [alo, ahi, blo, bhi] = queue.pop();
    const [i, j, k] = findLongestMatch(a, b, b2j, alo, ahi, blo, bhi);
    if (k) {
      matches += k;
      if (alo < i && blo < j) queue.push([alo, i, blo, j]);
      if (i + k < ahi && j + k < bhi) queue.push([i + k, ahi, j + k, bhi]);
    }
  }
  return 2.0 * matches / (la + lb);
}

function isSubset(a, b) {
  for (const x of a) if (!b.has(x)) return false;
  return true;
}

/** Build the in-memory screening index from the csl_entities JSON object. */
export function buildSanctionsIndex(data) {
  const entities = (data && data.entities) || [];
  const norms = new Array(entities.length);
  const inverted = new Map();
  const add = (tok, idx) => {
    const l = inverted.get(tok);
    if (!l) inverted.set(tok, [idx]);
    else if (l[l.length - 1] !== idx) l.push(idx);
  };
  for (let idx = 0; idx < entities.length; idx++) {
    const e = entities[idx];
    const [tokSet, normStr] = normalizeTokens(e.n ?? '');
    const altNorms = (e.a || []).map(normalizeTokens);
    norms[idx] = { tokSet, normStr, altNorms };
    for (const t of tokSet) add(t, idx);
    for (const [aSet] of altNorms) for (const t of aSet) add(t, idx);
  }
  return {
    entities,
    norms,
    inverted,
    count: Number.isFinite(Number(data?.count)) ? Math.trunc(Number(data.count)) : entities.length,
    snapshot_date: String(data?.snapshot_date ?? '2026-10-19'),
  };
}

/** screen(): SanctionsScreen for `query` (matches with score >= 0.86, best first). */
export function screenSanctions(index, query, limit = 10) {
  const q = String(query);
  const [qTokens, qNorm] = normalizeTokens(q);
  const cand = new Set();
  for (const t of qTokens) {
    const l = index.inverted.get(t);
    if (l) for (const i of l) cand.add(i);
  }
  const ordered = Array.from(cand).sort((x, y) => x - y);
  const raw = [];
  for (const idx of ordered) {
    const e = index.entities[idx];
    const meta = index.norms[idx];
    let bestScore = 0.0;
    let bestName = e.n ?? '';
    const cands = [[e.n ?? '', meta.tokSet, meta.normStr]];
    const alts = e.a || [];
    for (let k = 0; k < alts.length; k++) cands.push([alts[k], meta.altNorms[k][0], meta.altNorms[k][1]]);
    for (const [name, tokSet, norm] of cands) {
      let score = sequenceRatio(qNorm, norm);
      if (qTokens.size && isSubset(qTokens, tokSet)) {
        const boost = 0.88 + 0.12 * (qTokens.size / Math.max(tokSet.size, 1));
        score = Math.max(score, boost);
      }
      if (score > bestScore) { bestScore = score; bestName = name; }
    }
    if (bestScore >= 0.86) {
      raw.push({
        name: e.n ?? '',
        matched_name: bestName,
        score: pyRound(bestScore, 3),
        list_source: e.s ?? '',
        programs: e.p ?? null,
        country: e.c ?? null,
        entity_number: e.e ?? null,
        source_list_url: e.u ?? null,
      });
    }
  }
  raw.sort((x, y) => y.score - x.score); // stable: ties keep entity order
  return {
    query: q,
    matches: raw.slice(0, limit),
    list_size: index.count,
    snapshot_date: index.snapshot_date,
    provenance: {
      source: 'trade_csl',
      title: 'US Trade.gov Consolidated Screening List',
      url: CSL_URL,
      retrieved_at: utcnowIso(),
      license: 'Public domain (U.S. Government work)',
      locator: `Query: ${q}`,
    },
  };
}

/** Decode a (possibly gzip-compressed) JSON response body. */
export async function decodeMaybeGzipJson(response) {
  const buf = new Uint8Array(await response.arrayBuffer());
  if (buf.length >= 2 && buf[0] === 0x1f && buf[1] === 0x8b) {
    if (typeof DecompressionStream !== 'function') {
      throw codedError('gzip snapshot needs DecompressionStream (unsupported in this browser)', 'NO_DECOMPRESSION');
    }
    const stream = new Blob([buf]).stream().pipeThrough(new DecompressionStream('gzip'));
    return JSON.parse(await new Response(stream).text());
  }
  return JSON.parse(new TextDecoder('utf-8').decode(buf));
}

// ---------------------------------------------------------------------------
// IMF PortWatch (port of sentinel/sources/portwatch.py)
// ---------------------------------------------------------------------------

export const PORTWATCH = {
  META_URL: 'https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/PortWatch_chokepoints_database/FeatureServer/0/query',
  DAILY_URL: 'https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/Daily_Chokepoints_Data/FeatureServer/0/query',
  URL: 'https://portwatch.imf.org/pages/chokepoints',
  LICENSE: 'IMF PortWatch — free to use with attribution (see portwatch.imf.org terms)',
};

const pyStr = v => (v === null || v === undefined ? 'None' : String(v));

async function arcgisQuery(fetchImpl, url, params, signal, retries = 4) {
  const qs = new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v)])).toString();
  for (let attempt = 0; ; attempt++) {
    try {
      const res = await fetchImpl(`${url}?${qs}`, { signal, headers: { Accept: 'application/json' } });
      if (!res.ok) {
        throw codedError(`PortWatch HTTP ${res.status}`, 'PORTWATCH_HTTP', { status: res.status, retryable: res.status === 429 || res.status >= 500 });
      }
      const data = await res.json();
      if (data && data.error) throw codedError(`PortWatch: ${data.error.message || 'query failed'}`, 'PORTWATCH_ERROR', { retryable: true });
      return data;
    } catch (err) {
      const retryable = err && (err.retryable || err.name === 'TypeError'); // TypeError = network failure
      if (attempt >= retries || !retryable || (signal && signal.aborted)) throw err;
      await new Promise(resolve => setTimeout(resolve, Math.min(500 * 2 ** attempt, 4000))); // ArcGIS sends intermittent error bodies
    }
  }
}

function limiter(max) {
  let active = 0;
  const waiting = [];
  return async fn => {
    if (active >= max) await new Promise(resolve => waiting.push(resolve));
    active++;
    try { return await fn(); } finally { active--; const next = waiting.shift(); if (next) next(); }
  };
}

// The Daily_Chokepoints_Data layer serves at most 1000 records per request, so
// portwatch.py's 2000-record pages arrive as 1000-record pages at offsets
// 0, 1000, 2000, ... (~14 pages). We request exactly those pages concurrently
// (one round trip; <= `concurrency` in flight - the ArcGIS host speaks HTTP/2,
// so browsers multiplex them on one connection), then validate the chain the
// way the Python loop walks it; any surprise falls back to the sequential loop.
// Same where / outFields as portwatch.py. By default the pages use the server's
// ObjectId order instead of `orderByFields=date ASC`: the record set is the same
// and every port is re-sorted by date afterwards exactly as in Python, but the
// date sort makes each concurrent offset page ~3x slower server-side (measured
// cold: ~16 s vs ~6 s for the 14 pages). Pass {orderByFields: 'date ASC'} for
// the literal Python query.
export const PORTWATCH_SERVER_PAGE = 1000;
const PORTWATCH_PORTS_HINT = 28;

/**
 * Raw PortWatch pull (portwatch.py queries): chokepoint metadata, and daily
 * transits for the last 500 days paged with resultOffset.
 * Returns {meta, pages: [{offset, data}], retrieved_at}.
 */
export async function fetchPortWatchRaw({
  fetchImpl = globalThis.fetch, signal, nowMs = Date.now(), sequential = false, concurrency = 16, orderByFields = null,
} = {}) {
  const startDate = new Date(nowMs - 500 * 86400000).toISOString().slice(0, 10);
  const metaP = arcgisQuery(fetchImpl, PORTWATCH.META_URL, {
    where: '1=1', outFields: 'portid,portname,lat,lon', returnGeometry: 'false', f: 'json',
  }, signal);
  metaP.catch(() => {}); // awaited below; avoid an unhandled rejection while pages load
  const limit = limiter(Math.max(1, concurrency));
  const page = offset => limit(() => arcgisQuery(fetchImpl, PORTWATCH.DAILY_URL, {
    where: `date >= DATE '${startDate}'`,
    outFields: 'date,portid,portname,n_total,capacity',
    ...(orderByFields ? { orderByFields } : {}),
    resultOffset: offset,
    resultRecordCount: PORTWATCH_SERVER_PAGE,
    returnGeometry: 'false',
    f: 'json',
  }, signal));

  const pages = [];
  let offset = 0;
  let complete = false;
  if (!sequential) {
    const n = Math.ceil((PORTWATCH_PORTS_HINT * 502) / PORTWATCH_SERVER_PAGE) + 1;
    const offsets = Array.from({ length: n }, (_, i) => i * PORTWATCH_SERVER_PAGE);
    const batch = await Promise.all(offsets.map(page));
    for (let k = 0; k < batch.length; k++) {
      if (offsets[k] !== offset) break; // page size differs from the assumption: continue sequentially
      const data = batch[k];
      const features = data.features || [];
      if (!features.length) { complete = true; break; }
      pages.push({ offset, data });
      offset += features.length;
      if (!data.exceededTransferLimit) { complete = true; break; }
    }
  }
  for (let guard = 0; !complete && guard < 500; guard++) { // portwatch.py loop
    const data = await page(offset);
    const features = data.features || [];
    if (!features.length) break;
    pages.push({ offset, data });
    offset += features.length;
    if (!data.exceededTransferLimit) break;
  }
  const meta = await metaP;
  return { meta, pages, retrieved_at: utcnowIso() };
}

/** get_chokepoints() computation on raw PortWatch responses → {as_of, items}. */
export function chokepointsFromRaw(raw) {
  const meta = new Map();
  for (const f of (raw.meta && raw.meta.features) || []) {
    const attr = f.attributes || {};
    const pid = attr.portid;
    if (pid) {
      meta.set(pid, {
        name: attr.portname === undefined ? pid : attr.portname,
        lat: Number(attr.lat ?? 0.0),
        lon: Number(attr.lon ?? 0.0),
      });
    }
  }
  const byPort = new Map();
  for (const page of raw.pages || []) {
    for (const f of (page.data && page.data.features) || []) {
      const attr = f.attributes || {};
      const pid = attr.portid;
      if (!pid) continue;
      const l = byPort.get(pid);
      if (l) l.push(attr); else byPort.set(pid, [attr]);
    }
  }
  for (const l of byPort.values()) {
    l.sort((x, y) => {
      const a = pyStr(x.date), b = pyStr(y.date);
      return a < b ? -1 : a > b ? 1 : 0;
    });
  }

  const items = [];
  const latestDates = [];
  const r2 = v => (v === null ? null : pyRound(v, 2));
  for (const [portid, m] of meta) {
    const ptsRaw = byPort.get(portid) || [];
    if (!ptsRaw.length) continue;
    const pts = ptsRaw.map(p => ({
      date: pyStr(p.date),
      n_total: Math.trunc(Number(p.n_total) || 0),
      capacity: p.capacity === null || p.capacity === undefined ? null : Number(p.capacity),
    }));
    const latest = pts[pts.length - 1];
    latestDates.push(latest.date);

    const n7 = pts.slice(-7).map(p => p.n_total);
    const mean7 = n7.length ? npMean(n7) : null;
    const prior = pts.slice(0, Math.max(0, pts.length - 7));
    const basePts = prior.length >= 365 ? prior.slice(-365) : prior;
    const nBase = basePts.map(p => p.n_total);
    let baseline = null, delta = null, zscore = null;
    if (nBase.length) {
      baseline = npMean(nBase);
      const std = npStd(nBase);
      delta = baseline && mean7 !== null ? ((mean7 / baseline) - 1.0) * 100.0 : null;
      zscore = std > 0 && mean7 !== null ? (mean7 - baseline) / std : null;
    }
    items.push({
      id: portid,
      name: pyStr(m.name),
      lat: m.lat,
      lon: m.lon,
      latest,
      mean_7d: r2(mean7),
      baseline_365d: r2(baseline),
      delta_pct: r2(delta),
      zscore: r2(zscore),
      series: pts.length >= 120 ? pts.slice(-120) : pts,
      provenance: {
        source: 'imf_portwatch',
        title: `IMF PortWatch Daily Chokepoint Transits — ${pyStr(m.name)}`,
        url: PORTWATCH.URL,
        retrieved_at: raw.retrieved_at,
        license: PORTWATCH.LICENSE,
        locator: portid,
      },
    });
  }
  const portNum = id => {
    const n = parseInt(String(id).replace('chokepoint', ''), 10);
    return Number.isFinite(n) ? n : Number.MAX_SAFE_INTEGER;
  };
  items.sort((x, y) => portNum(x.id) - portNum(y.id));
  let asOf = utcnowIso().slice(0, 10);
  if (latestDates.length) asOf = latestDates.reduce((a, b) => (b > a ? b : a));
  return { as_of: asOf, items };
}

// ---------------------------------------------------------------------------
// Snapshot API
// ---------------------------------------------------------------------------

export function normalizeTicker(t) {
  const u = String(t ?? '').trim().toUpperCase();
  return TICKER_ALIASES[u] || u;
}

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

/** Per-event replay delays (ms): t_ms gaps capped at 400 ms, / speed, total <= 12 s. */
export function replayDelays(events, speed = 1, { capMs = 400, totalMs = 12000 } = {}) {
  const sp = Number(speed) > 0 ? Number(speed) : 1;
  let prev = 0;
  const gaps = (events || []).map(e => {
    const t = Number(e && e.t_ms) || 0;
    const g = Math.min(capMs, Math.max(0, t - prev));
    prev = t;
    return g / sp;
  });
  const total = gaps.reduce((a, b) => a + b, 0);
  return total > totalMs ? gaps.map(g => g * (totalMs / total)) : gaps;
}

/**
 * createSnapshotApi({snapshotBase, fetchImpl, liveTimeoutMs}) → object whose async
 * methods mirror /api/v1/* (see docs/API.md) on top of the static snapshots.
 */
export function createSnapshotApi({ snapshotBase = './snapshots', fetchImpl = globalThis.fetch, liveTimeoutMs = 8000 } = {}) {
  const base = String(snapshotBase).replace(/\/+$/, '');
  const doFetch = (url, init) => fetchImpl(url, init);
  const fileCache = new Map();
  const ciks = new Map();
  let sanctionsIndexP = null;
  let liveChk = null; // {at, value} | {at, error}
  let liveChkP = null;

  function cached(key, loader) {
    let p = fileCache.get(key);
    if (!p) {
      p = loader();
      fileCache.set(key, p);
      p.catch(() => { if (fileCache.get(key) === p) fileCache.delete(key); });
    }
    return p;
  }

  async function fetchFile(path) {
    const res = await doFetch(`${base}/${path}`);
    if (!res.ok) {
      throw codedError(`snapshot ${path}: HTTP ${res.status}`, res.status === 404 ? 'SNAPSHOT_MISSING' : 'SNAPSHOT_HTTP', { status: res.status });
    }
    return res;
  }

  const json = path => cached(path, async () => (await fetchFile(path)).json());
  const loadIndex = () => json('index.json');

  async function resolveTicker(t) {
    const idx = await loadIndex();
    const items = idx.items || [];
    const norm = normalizeTicker(t);
    const tries = [norm, norm.replace(/\./g, '-'), norm.replace(/-/g, '.')];
    const hit = items.find(i => tries.includes(i.ticker));
    if (!hit) {
      throw codedError(`${norm || '(empty)'} is not in the static snapshot (${items.length} tickers covered)`, 'NOT_IN_SNAPSHOT', {
        status: 404, ticker: norm, coverage: items.map(i => i.ticker),
      });
    }
    return hit.ticker;
  }

  async function tickerFile(t, name) {
    const T = await resolveTicker(t);
    const obj = await json(`${T}/${name}`);
    const cik = name === 'company.json' ? obj.cik : name === 'analysis.json' ? obj.company?.cik : null;
    if (cik) ciks.set(T, cik);
    return obj;
  }

  async function loadAnalysis(t) { return tickerFile(t, 'analysis.json'); }

  function sanctionsIndex() {
    if (!sanctionsIndexP) {
      sanctionsIndexP = (async () => buildSanctionsIndex(await decodeMaybeGzipJson(await fetchFile('csl_entities.json.gz'))))();
      sanctionsIndexP.catch(() => { sanctionsIndexP = null; });
    }
    return sanctionsIndexP;
  }

  // One shared live pull. It is not aborted when a caller's 8 s budget runs
  // out: it keeps going (hard cap PORTWATCH_HARD_CAP_MS) so the next call - or
  // an `onLive` callback - gets live data once it lands.
  const PORTWATCH_HARD_CAP_MS = 45000;
  function startLivePull() {
    if (!liveChkP) {
      liveChkP = (async () => {
        const ctrl = typeof AbortController === 'function' ? new AbortController() : null;
        const hard = setTimeout(() => {
          if (ctrl) ctrl.abort(codedError(`PortWatch live query exceeded ${PORTWATCH_HARD_CAP_MS} ms`, 'PORTWATCH_TIMEOUT'));
        }, PORTWATCH_HARD_CAP_MS);
        try {
          const out = chokepointsFromRaw(await fetchPortWatchRaw({ fetchImpl: doFetch, signal: ctrl?.signal }));
          if (!out.items.length) throw codedError('PortWatch returned no chokepoints', 'PORTWATCH_EMPTY');
          liveChk = { at: Date.now(), value: out };
          return out;
        } catch (err) {
          liveChk = { at: Date.now(), error: err };
          throw err;
        } finally {
          clearTimeout(hard);
          liveChkP = null;
        }
      })();
      liveChkP.catch(() => {});
    }
    return liveChkP;
  }

  async function liveChokepoints(onLive) {
    const LIVE_TTL = 30 * 60 * 1000, FAIL_TTL = 2 * 60 * 1000;
    const now = Date.now();
    if (liveChk && liveChk.value && now - liveChk.at < LIVE_TTL) return liveChk.value;
    if (!liveChkP && liveChk && liveChk.error && now - liveChk.at < FAIL_TTL) throw liveChk.error;
    const pull = startLivePull();
    let timer;
    const budget = new Promise((_, reject) => {
      timer = setTimeout(() => reject(codedError(`PortWatch live query exceeded ${liveTimeoutMs} ms`, 'PORTWATCH_TIMEOUT', { pending: true })), liveTimeoutMs);
    });
    try {
      return await Promise.race([pull, budget]);
    } catch (err) {
      if (err && err.pending && typeof onLive === 'function') {
        pull.then(out => onLive({ as_of: out.as_of, items: clone(out.items), source: 'live' }), () => {});
      }
      throw err;
    } finally {
      clearTimeout(timer);
    }
  }

  const api = {
    mode: 'snapshot',
    snapshotBase: base,

    async health() {
      const idx = await loadIndex();
      return {
        status: 'snapshot',
        version: idx.version || SNAPSHOT_API_VERSION,
        llm: { enabled: false, model: null },
        generated_at: idx.generated_at,
        scenario: clone(idx.scenario),
        coverage: (idx.items || []).length,
        sources: {
          sec_edgar: 'snapshot', imf_portwatch: 'live (browser) + snapshot', un_comtrade: 'snapshot', worldbank_cmo: 'snapshot',
          usgs_mcs: 'snapshot', trade_csl: 'snapshot', nyfed_gscpi: 'snapshot',
        },
      };
    },

    async index() { return clone(await loadIndex()); },

    async search(q) {
      const query = String(q ?? '').trim().toUpperCase();
      if (!query) return [];
      const items = (await loadIndex()).items || [];
      const exact = [], prefix = [], names = [];
      for (const it of items) {
        if (it.ticker === query) exact.push(it);
        else if (it.ticker.startsWith(query)) prefix.push(it);
        else if (query.length >= 3 && String(it.name || '').toUpperCase().includes(query)) names.push(it);
      }
      prefix.sort((a, b) => a.ticker.length - b.ticker.length);
      return [...exact, ...prefix, ...names].slice(0, 12)
        .map(it => ({ ticker: it.ticker, cik: ciks.get(it.ticker) ?? null, name: it.name }));
    },

    async company(t) { return clone(await tickerFile(t, 'company.json')); },
    async exposure(t) { return clone(await tickerFile(t, 'exposure.json')); },
    async analysis(t) { return clone(await loadAnalysis(t)); },

    /**
     * {as_of, items, source: 'live' | 'snapshot'}. Live = IMF PortWatch queried
     * from the browser; on error or after `liveTimeoutMs` (8 s) the snapshot is
     * returned (`live_error`, and `live_pending: true` when the pull is still
     * running - pass `onLive` to receive the live payload when it arrives).
     */
    async chokepoints({ live = true, onLive } = {}) {
      let liveError = null;
      if (live) {
        try {
          const out = await liveChokepoints(onLive);
          return { as_of: out.as_of, items: clone(out.items), source: 'live' };
        } catch (err) {
          liveError = err;
        }
      }
      const snap = await json('chokepoints.json');
      const out = { as_of: snap.as_of, items: clone(snap.items), source: 'snapshot' };
      if (liveError) {
        out.live_error = String(liveError.message || liveError);
        if (liveError.pending) out.live_pending = true;
      }
      return out;
    },

    async minerals() { return clone(await json('minerals.json')); },
    async macro() { return clone(await json('macro.json')); },

    async sanctions(q) {
      const query = String(q ?? '');
      if (query.length < 2 || query.length > 120) {
        throw codedError('sanctions query must be 2-120 characters', 'INVALID_QUERY', { status: 422 });
      }
      return screenSanctions(await sanctionsIndex(), query);
    },

    /**
     * Scenario stress (fast path of /api/v1/stress/{ticker}) using the browser
     * replica. Pass {exactAtSnapshotScenario: true} to return the Python
     * sandbox result unchanged when the scenario equals the snapshot's own.
     */
    async stress(t, scenario = {}, { exactAtSnapshotScenario = false } = {}) {
      const a = await loadAnalysis(t);
      const sc = { ...DEFAULT_SCENARIO, ...(a.scenario || {}) };
      for (const k of [...CHANNELS, 'horizon']) {
        if (scenario && scenario[k] !== undefined && scenario[k] !== null && scenario[k] !== '') sc[k] = Number(scenario[k]);
      }
      for (const k of CHANNELS) sc[k] = Math.max(0, Math.min(1, Number.isFinite(sc[k]) ? sc[k] : DEFAULT_SCENARIO[k]));
      sc.horizon = Math.max(2026, Math.min(2030, Math.trunc(Number.isFinite(sc.horizon) ? sc.horizon : DEFAULT_SCENARIO.horizon)));
      const exposureByChannel = clone(a.engine?.exposure_by_channel || a.stress?.inputs?.exposure || {});
      const snapSc = a.scenario || {};
      const same = [...CHANNELS, 'horizon'].every(k => Math.abs(Number(snapSc[k]) - sc[k]) < 1e-9);
      if (exactAtSnapshotScenario && same) {
        return { stress: clone(a.stress), metrics: clone(a.metrics), exposure_by_channel: exposureByChannel, mode: 'snapshot' };
      }
      const stress = runStressReplica({ ...a.stress.inputs, __code: a.stress.code || '' }, sc);
      const metrics = recomputeScenarioMetrics(a.metrics, stress);
      return { stress, metrics, exposure_by_channel: exposureByChannel, mode: 'replica' };
    },

    /**
     * Replay the snapshot run's AgentEvents with their t_ms spacing (each gap
     * capped at 400 ms, divided by `speed`, total <= 12 s), then the result.
     * Returns a cancel function (also exposed as `.close`; `.done` resolves to
     * 'done' | 'cancelled' | 'error').
     */
    analyzeReplay(t, { onEvent, onResult, onError, speed = 1 } = {}) {
      let cancelled = false;
      let finish;
      const done = new Promise(resolve => { finish = resolve; });
      const report = err => (typeof globalThis.reportError === 'function' ? globalThis.reportError(err) : console.error(err));
      const safe = (fn, arg) => { try { if (fn) fn(arg); } catch (err) { report(err); } };
      (async () => {
        let result;
        try {
          result = clone(await loadAnalysis(t));
        } catch (err) {
          if (!cancelled) { safe(onError, err); finish('error'); }
          return;
        }
        const events = result.events || [];
        const delays = replayDelays(events, speed);
        for (let i = 0; i < events.length; i++) {
          if (delays[i] > 0) await sleep(delays[i]);
          if (cancelled) return;
          safe(onEvent, events[i]);
        }
        if (cancelled) return;
        safe(onResult, result);
        finish('done');
      })();
      const cancel = () => {
        if (cancelled) return;
        cancelled = true;
        finish('cancelled');
      };
      cancel.close = cancel;
      cancel.done = done;
      return cancel;
    },
  };
  return api;
}
