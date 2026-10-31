// Entry router. `?mode=pro` → Expert mode (the existing terminal, js/app.js, unchanged).
// Otherwise → Brief (js/brief.js) when the portfolio snapshot exists; with `?mock=1` the
// brief reads the FIXTURE files in public/mock/. Without either, the terminal landing is shown.
const params = new URLSearchParams(location.search);
const pro = params.get('mode') === 'pro';
const mock = params.get('mock') === '1';
const KEEP = ['t', 'mock', 'api', 'book'];
const $ = id => document.getElementById(id);

export function modeURL(target, extra = {}) {
  const now = new URLSearchParams(location.search), next = new URLSearchParams();
  for (const k of KEEP) if (now.get(k)) next.set(k, now.get(k));
  if (target === 'pro') next.set('mode', 'pro');
  for (const [k, v] of Object.entries(extra)) { if (v == null || v === '') next.delete(k); else next.set(k, v); }
  const qs = next.toString();
  return `${location.pathname}${qs ? `?${qs}` : ''}`;
}

async function readJSON(url) {
  try { const r = await fetch(url, { cache: 'no-cache' }); return r.ok ? await r.json() : null; } catch { return null; }
}

async function terminal(briefAvailable) {
  document.body.classList.remove('route-pending', 'brief');
  document.body.classList.add('pro');
  $('brief').hidden = true; $('terminal').hidden = false;
  const link = $('brief-link');
  if (!briefAvailable) link.hidden = true;
  else link.addEventListener('click', e => { e.preventDefault(); location.href = modeURL('brief'); });
  await import('./app.js');
}

async function route() {
  if (pro) return terminal(true);
  let base = './snapshots';
  if (!mock) { const cfg = await readJSON('./config.json'); if (cfg?.snapshot_base) base = cfg.snapshot_base.replace(/\/$/, ''); }
  const book = params.get('book') === 'coverage40' ? 'coverage40' : 'kic13f';
  const portfolioURL = mock ? `./mock/portfolio_${book}.json` : `${base}/portfolio/${book}.json`;
  const validationURL = mock ? './mock/validation_events.json' : `${base}/validation/events.json`;
  const portfolio = await readJSON(portfolioURL);
  if (!portfolio) return terminal(false);
  const validation = await readJSON(validationURL);
  $('terminal').hidden = true; $('brief').hidden = false;
  document.body.classList.remove('route-pending');
  document.body.classList.add('brief');
  const { mountBrief } = await import('./brief.js');
  mountBrief({ portfolio, validation, book, mock, base, portfolioURL, validationURL, modeURL });
}

route().catch(error => { console.error(error); terminal(false); });
