// Formatting, labels and small SVG marks shared by the Brief (L0) and the detail drawer (L1).
import { pyRound } from './stress_replica.js';
export const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
export const MINUS = '−';
const finite = v => v != null && v !== '' && Number.isFinite(Number(v));

/** ASCII rounded string (what `data-value` carries), rounded like Python's round()/format (ties to even); never "-0.0". */
export function raw(v, d = 1) {
  const s = pyRound(Number(v), d).toFixed(d);
  return Number(s) === 0 ? (0).toFixed(d) : s;
}
/** Display string with a true minus sign; `sign` adds "+" to positives. */
export function fmt(v, d = 1, { sign = false } = {}) {
  if (!finite(v)) return '—';
  const r = raw(v, d), n = Number(r);
  const body = Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
  return (n < 0 ? MINUS : sign && n > 0 ? '+' : '') + body;
}
/**
 * A number element. `src` = "<file>#/<json pointer>" of the record it was read from,
 * `ev`/`ledger` = evidence id and the ledger it belongs to. `data-value` is the
 * rounded value exactly as displayed (ASCII), for recomputation checks.
 */
export function num(v, { d = 1, sign = false, unit = '', src = '', ev = '', ledger = '', cls = '', title = '' } = {}) {
  if (!finite(v)) return `<span class="num ${cls}">—</span>`;
  const attrs = [`data-value="${raw(v, d)}"`];
  if (src) attrs.push(`data-source="${esc(src)}"`);
  if (ev) attrs.push(`data-evidence="${esc(ev)}"`);
  if (ledger) attrs.push(`data-ledger="${esc(ledger)}"`);
  if (title) attrs.push(`title="${esc(title)}"`);
  return `<span class="num ${cls}" ${attrs.join(' ')}>${fmt(v, d, { sign })}${unit ? `<span class="unit">${esc(unit)}</span>` : ''}</span>`;
}
export function dateOf(s) { const m = String(s ?? '').match(/\d{4}-\d{2}-\d{2}/); return m ? m[0] : '—'; }
export function stamp(s) { const m = String(s ?? '').match(/(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/); return m ? `${m[1]} ${m[2]} UTC` : dateOf(s); }
export const isFixture = s => /^FIXTURE/.test(String(s ?? ''));
export const unfix = s => String(s ?? '').replace(/^FIXTURE\s*/, '');

export const VERDICT = {
  REDUCE: { ko: '노출 축소 검토', en: 'Reduce', cls: 'reduce' },
  HEDGE: { ko: '헤지 검토', en: 'Hedge', cls: 'hedge' },
  HOLD: { ko: '모니터링', en: 'Monitor', cls: 'hold' },
  ACCUMULATE: { ko: '여력 있음', en: 'Capacity', cls: 'capacity' },
};
export const VERDICT_ORDER = ['REDUCE', 'HEDGE', 'HOLD', 'ACCUMULATE'];
export function chip(code, { small = false } = {}) {
  const v = VERDICT[String(code || '').toUpperCase()];
  if (!v) return `<span class="v-chip">${esc(code || '—')}</span>`;
  return `<span class="v-chip v-${v.cls}${small ? ' v-sm' : ''}"><i aria-hidden="true"></i><b>${v.ko}</b><small>${v.en}</small></span>`;
}

export const CHANNELS = {
  taiwan: { ko: '대만 해협·대만 공급', short: '대만', en: 'Taiwan Strait & supply', cp: 'chokepoint11' },
  china_export: { ko: '중국 핵심광물 수출통제', short: '중국 수출통제', en: 'China export controls', cp: null },
  hormuz: { ko: '호르무즈 해협', short: '호르무즈', en: 'Strait of Hormuz', cp: 'chokepoint6' },
  malacca: { ko: '말라카 해협', short: '말라카', en: 'Malacca Strait', cp: 'chokepoint5' },
  weaponized: { ko: '자원 무기화 전반', short: '무기화', en: 'Weaponized resources', cp: null },
};
export const CHANNEL_KEYS = ['taiwan', 'china_export', 'hormuz', 'malacca'];

export const METRICS = {
  CPI: { ko: '병목 의존도', desc: '전력·용수·광물·해상로 같은 물리적 병목에 매출이 얼마나 기대는지' },
  GPI: { ko: '지정학 취약도', desc: '수출통제·관세·제재 언어와 중국·대만 집중도' },
  SRS: { ko: '시나리오 충격', desc: '시나리오에서 현금흐름이 20% 넘게 줄어들 확률과 꼬리 손실' },
  FAR: { ko: '우호국 전환 여력', desc: '충격을 흡수하고 공급망을 옮길 재무 여력 (높을수록 양호)' },
};

export const SOURCES = {
  sec_edgar: ['SEC EDGAR', '공시·재무'], imf_portwatch: ['IMF PortWatch', '해상 통항 (AIS)'], usgs_mcs: ['USGS MCS', '광물 생산 집중도'],
  un_comtrade: ['UN Comtrade', '교역 의존도'], trade_csl: ['Trade.gov CSL', '제재 명단'], fred: ['FRED', '거시 지표'], nyfed_gscpi: ['NY Fed GSCPI', '공급망 압력'],
  sandbox: ['샌드박스 계산', '시드 고정 코드 실행'], model: ['모델 계산', '공개 방법론'],
};
export const sourceName = s => (SOURCES[s] || [String(s || '출처').toUpperCase()])[0];

export const KO_CHOKEPOINTS = {
  'Suez Canal': '수에즈 운하', 'Panama Canal': '파나마 운하', 'Bosporus Strait': '보스포루스 해협', 'Bab el-Mandeb Strait': '바브엘만데브 해협',
  'Malacca Strait': '말라카 해협', 'Strait of Hormuz': '호르무즈 해협', 'Cape of Good Hope': '희망봉', 'Gibraltar Strait': '지브롤터 해협',
  'Dover Strait': '도버 해협', 'Oresund Strait': '외레순 해협', 'Taiwan Strait': '대만 해협', 'Korea Strait': '대한 해협', 'Tsugaru Strait': '쓰가루 해협',
  'Luzon Strait': '루손 해협', 'Lombok Strait': '롬복 해협', 'Ombai Strait': '옴바이 해협', 'Bohai Strait': '보하이 해협', 'Torres Strait': '토레스 해협',
  'Sunda Strait': '순다 해협', 'Makassar Strait': '마카사르 해협', 'Magellan Strait': '마젤란 해협', 'Yucatan Channel': '유카탄 해협',
  'Windward Passage': '윈드워드 해협', 'Mona Passage': '모나 해협', 'Balabac Strait': '발라박 해협', 'Bering Strait': '베링 해협',
  'Mindoro Strait': '민도로 해협', 'Kerch Strait': '케르치 해협',
};
export const koChokepoint = name => KO_CHOKEPOINTS[name] || name || '—';

/** Same eligibility rule as js/app.js anomaly(): largest absolute shortfall among Δ ≤ −25%, else the steepest Δ. */
export function anomaly(items = []) {
  const ok = items.filter(x => Number(x.baseline_365d) >= 10);
  return ok.filter(x => Number(x.delta_pct) <= -25).sort((a, b) => (b.baseline_365d - b.mean_7d) - (a.baseline_365d - a.mean_7d))[0]
    || ok.filter(x => Number.isFinite(Number(x.delta_pct))).sort((a, b) => a.delta_pct - b.delta_pct)[0] || null;
}

/** Text with [E#] tags: the number right before a tag carries the evidence; the tag becomes a small source link. */
export function evidenceText(text, ledger) {
  return esc(text).replace(/([+\-−]?\$?\d[\d,.]*(?:\s?(?:%p|%|pp|B|척\/일))?)(\s*)\[(E\d+)\]/g,
    (_, n, sp, id) => `<span class="ev-num" data-evidence="${id}" data-ledger="${esc(ledger)}">${n.replace(/^-/, MINUS)}</span><button type="button" class="ev-ref" data-open-evidence="${id}" data-ledger="${esc(ledger)}" aria-label="근거 ${id}">${id}</button>`)
    .replace(/\[(E\d+)\]/g, (_, id) => `<button type="button" class="ev-ref" data-open-evidence="${id}" data-ledger="${esc(ledger)}" aria-label="근거 ${id}">${id}</button>`);
}

/** Line chart: series vs a baseline (dashed). Returns SVG markup. */
export function lineChart(points, { baseline = null, width = 552, height = 150, label = '' } = {}) {
  const ys = points.map(p => Number(p.v)).filter(Number.isFinite);
  if (ys.length < 2) return '';
  const max = Math.max(...ys, baseline ?? 0) * 1.08 || 1, min = 0, padL = 34, padB = 22, padT = 8, w = width - padL - 6, h = height - padB - padT;
  const x = i => padL + (i / (points.length - 1)) * w, y = v => padT + h - ((v - min) / (max - min)) * h;
  const line = points.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(Number(p.v)).toFixed(1)}`).join('');
  const ticks = [0, max / 2, max].map(v => `<g><line x1="${padL}" x2="${padL + w}" y1="${y(v)}" y2="${y(v)}" class="gl"/><text x="${padL - 6}" y="${y(v) + 3}" text-anchor="end">${Math.round(v)}</text></g>`).join('');
  const base = baseline != null ? `<line x1="${padL}" x2="${padL + w}" y1="${y(baseline)}" y2="${y(baseline)}" class="bl"/><text x="${padL + w}" y="${y(baseline) - 5}" text-anchor="end" class="bl-label">365일 평균 ${fmt(baseline, 1)}</text>` : '';
  return `<svg class="chart" viewBox="0 0 ${width} ${height}" width="100%" role="img" aria-label="${esc(label)}">${ticks}${base}<path d="${line}" class="sl"/><text x="${padL}" y="${height - 5}">${esc(points[0].t)}</text><text x="${padL + w}" y="${height - 5}" text-anchor="end">${esc(points.at(-1).t)}</text></svg>`;
}

/** Histogram with markers (value positions on the bin-edge axis). */
export function histogram(counts = [], edges = [], markers = [], { width = 552, height = 76 } = {}) {
  if (!counts.length || edges.length < 2) return '';
  const max = Math.max(1, ...counts), lo = edges[0], hi = edges.at(-1), padB = 18, h = height - padB - 4;
  const bw = width / counts.length, x = v => ((v - lo) / ((hi - lo) || 1)) * width;
  const bars = counts.map((c, i) => `<rect x="${(i * bw + 1).toFixed(1)}" y="${(4 + h - (c / max) * h).toFixed(1)}" width="${Math.max(1, bw - 2).toFixed(1)}" height="${((c / max) * h).toFixed(1)}"/>`).join('');
  const marks = markers.filter(m => Number.isFinite(m.v)).map((m, i) => { const mx = x(m.v), right = mx > width - 96; return `<g class="mark"><line x1="${mx}" x2="${mx}" y1="2" y2="${4 + h}"/><text x="${right ? mx - 5 : mx + 5}" y="${12 + i * 13}" text-anchor="${right ? 'end' : 'start'}">${esc(m.label)}</text></g>`; }).join('');
  return `<svg class="hist" viewBox="0 0 ${width} ${height}" width="100%" role="img" aria-label="분포">${bars}${marks}<text x="0" y="${height - 3}">${fmt(lo, 0)}%</text><text x="${width}" y="${height - 3}" text-anchor="end">${fmt(hi, 0)}%</text></svg>`;
}
