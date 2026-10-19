const params = new URLSearchParams(location.search);
export const mock = params.get('mock') === '1';
const statusListeners = new Set();
export function onLinkStatus(fn) { statusListeners.add(fn); }
function link(down) { for (const fn of statusListeners) fn(down); }
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const mockNames = {
  health: 'health.json', search: 'search_nv.json', chokepoints: 'chokepoints.json',
  minerals: 'minerals.json', macro: 'macro.json', sanctions: 'sanctions_huawei.json'
};
function mockFile(kind, ticker) { return kind === 'analyze' ? `analyze_${ticker || 'NVDA'}.events.json` : mockNames[kind] || `${kind}_${ticker || 'NVDA'}.json`; }
export async function getJSON(kind, path, ticker, {signal} = {}) {
  if (mock) {
    const file = mockFile(kind, ticker);
    let response = await fetch(`/mock/${file}`, {signal});
    if (!response.ok && ticker && ticker !== 'NVDA') {
      response = await fetch(`/mock/${mockFile(kind, 'NVDA')}`, {signal});
      if (response.ok) document.dispatchEvent(new CustomEvent('mock-fallback', {detail: `${ticker} → NVDA ${kind}`}));
    }
    if (!response.ok) throw new Error(`${file} unavailable`);
    return response.json();
  }
  let wait = 500;
  for (let attempt = 0; attempt < 4; attempt++) {
    try {
      const response = await fetch(`/api/v1/${path}`, {signal, headers: {Accept: 'application/json'}});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      link(false);
      return response.json();
    } catch (error) {
      if (signal?.aborted) throw error;
      link(true);
      if (attempt === 3) throw error;
      await delay(wait); wait *= 2;
    }
  }
}
export async function search(q) { return getJSON('search', `search?q=${encodeURIComponent(q)}`); }
export function scenarioQuery(s) { return new URLSearchParams({taiwan:s.taiwan, china_export:s.china_export, hormuz:s.hormuz, malacca:s.malacca, horizon:s.horizon}).toString(); }
export function analyze(ticker, scenario, question, onEvent, onResult, onError) {
  if (mock) {
    let canceled = false;
    (async () => {
      try {
        const events = await getJSON('analyze', '', ticker);
        let previous = 0;
        for (const row of events) {
          if (canceled) return;
          const event = row.event || (row.data?.run_id ? 'result' : 'agent');
          const data = typeof row.data === 'string' ? JSON.parse(row.data) : row.data;
          const t = row.t_ms ?? data?.t_ms ?? previous;
          await delay(Math.max(0, (t - previous) / 3)); previous = t;
          if (event === 'result') onResult(data);
          else if (event === 'error') onError(data?.message || 'Analysis failed');
          else onEvent(data);
        }
      } catch (error) { if (!canceled) onError(error.message); }
    })();
    return {close() { canceled = true; }};
  }
  const qs = scenarioQuery(scenario) + `&ticker=${encodeURIComponent(ticker)}&question=${encodeURIComponent(question || '')}`;
  const stream = new EventSource(`/api/v1/analyze?${qs}`);
  stream.addEventListener('agent', e => onEvent(JSON.parse(e.data)));
  stream.addEventListener('result', e => { stream.close(); onResult(JSON.parse(e.data)); });
  stream.addEventListener('error', e => { stream.close(); onError(e.data ? JSON.parse(e.data).message : 'Stream disconnected'); });
  return stream;
}
