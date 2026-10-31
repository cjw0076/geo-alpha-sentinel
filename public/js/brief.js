// L0 Brief / 오늘의 판단: portfolio verdict, three numbers, holdings to review, signal globe, one input.
// Data: snapshots/portfolio/<book>.json (eng-01 §3) and snapshots/validation/events.json (eng-02 §6);
// FIXTURE copies in public/mock/ with ?mock=1. Detail drawer (L1): js/brief-drawer.js.
import {
  esc, num, fmt, raw, dateOf, chip, VERDICT, VERDICT_ORDER, CHANNELS, CHANNEL_KEYS, isFixture, unfix, anomaly,
  koChokepoint, evidenceText, sourceName,
} from './brief-format.js';
import { createSnapshotApi } from './snapshot.js';
import { createDrawer } from './brief-drawer.js';

const DOCS = 'https://github.com/cjw0076/geo-alpha-sentinel/blob/main/docs/';
const $ = id => document.getElementById(id);
const MARK = '<svg class="b-mark" viewBox="0 0 32 32" width="22" height="22" aria-hidden="true"><path d="M3 12V3h9M20 3h9v9M29 20v9h-9M12 29H3v-9" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="square"/><circle cx="16" cy="16" r="4.2" fill="#b7791f"/></svg>';
const KO_NAMES = { 엔비디아: 'NVDA', 애플: 'AAPL', 마이크로소프트: 'MSFT', 테슬라: 'TSLA', TSMC: 'TSM', 대만반도체: 'TSM', 인텔: 'INTC', 아마존: 'AMZN', 구글: 'GOOGL', 알파벳: 'GOOGL', 메타: 'META', 브로드컴: 'AVGO', 마이크론: 'MU', 퀄컴: 'QCOM', 셰브론: 'CVX', 록히드: 'LMT', 보잉: 'BA', 캐터필러: 'CAT', ASML: 'ASML', 앨버말: 'ALB' };
const CHANNEL_WORDS = [[/대만|taiwan|tsmc|반도체/i, 'taiwan'], [/호르무즈|hormuz|이란|중동|원유|유가/i, 'hormuz'], [/말라카|malacca|남중국해|해상로/i, 'malacca'], [/중국|china|수출\s?통제|갈륨|게르마늄|희토류|광물/i, 'china_export']];

let S = null;

export function prettyName(s) {
  const str = String(s ?? '');
  if (!str || /[a-z]/.test(unfix(str))) return str;
  const keep = new Set(['FIXTURE', 'KLA', 'ASML', 'AMD', 'ARM', 'NXP', 'GE', 'GM', 'RTX', 'TE', 'IBM', 'HP', 'AT&T', 'ON', 'NV', 'USA', 'US', 'II', 'III']);
  return str.replace(/\s*\/[A-Z]{2,3}\s*$/, '').split(/(\s+|,)/).map(w => keep.has(w) || !/[A-Z]/.test(w) ? w : w.length <= 2 && !['CO', 'OF', 'DE'].includes(w) ? w : w[0] + w.slice(1).toLowerCase()).join('');
}

function srcOf(url) { return String(url || '').replace(/^\.\//, ''); }

export function headlineChannel(p) {
  const chs = p.channels || [], h = p.headline || {};
  const named = h.channel_id || h.channel;
  if (named) { const c = chs.find(x => x.id === named); if (c) return c; }
  const text = `${unfix(h.ko)} ${unfix(h.en)}`;
  const hit = chs.find(x => (unfix(x.label_ko) && text.includes(unfix(x.label_ko))) || (unfix(x.label_en) && text.includes(unfix(x.label_en))));
  if (hit) return hit;
  return [...chs].sort((a, b) => Math.abs(b.active_pp || 0) - Math.abs(a.active_pp || 0))[0] || null;
}

function evidenceOf(id) { return (S.portfolio.evidence || []).find(e => e.id === id) || null; }

function chipLabel(e) {
  const p = e?.provenance || {}, src = p.source, title = unfix(p.title), label = unfix(e?.label), t = `${title} ${label}`;
  if (src === 'sec_edgar') return /N-?PORT|ACWI/i.test(t) ? '벤치마크 N-PORT' : /13F/i.test(t) ? 'KIC 13F' : 'SEC 공시';
  if (src === 'imf_portwatch') { const m = title.match(/—\s*(.+)$/) || label.match(/^([^:]+):/); return m ? `${koChokepoint(m[1].trim())} 통항` : '해상 통항'; }
  if (src === 'usgs_mcs') return '광물 집중도';
  if (/threshold/i.test(t)) return '실질 노출 기준 (노출도 ≥ 0.5)';
  if (/weight share of covered holdings/i.test(t)) return /ACWI|benchmark/i.test(t) ? '벤치마크 실질 노출 비중' : 'KIC 보유 실질 노출 비중';
  if (/active exposure/i.test(t)) return '벤치마크 대비 초과 노출';
  if (/exposure-weighted share/i.test(t)) return '보유 노출 비중';
  if (/FCF shock|Monte Carlo/i.test(t)) return '공동 시나리오';
  if (src === 'model' || src === 'sandbox') return '모델 계산';
  return unfix(e?.label).slice(0, 22);
}

function renderTop() {
  const p = S.portfolio, b = p.book || {};
  return `<header class="b-top"><div class="b-wrap b-top-in">
    <a class="b-brand" href="${esc(S.modeURL('brief', { t: null, c: null }))}" aria-label="Geo-Alpha Sentinel 브리프">${MARK}<span>GEO-ALPHA SENTINEL</span></a>
    <div class="b-book" role="group" aria-label="포트폴리오 선택">
      <button type="button" data-book="kic13f" aria-pressed="${S.book === 'kic13f'}">KIC 13F</button><button type="button" data-book="coverage40" aria-pressed="${S.book === 'coverage40'}">커버리지 40</button>
    </div>
    <p class="b-asof"><span>보유 기준일</span> <time data-source="${esc(S.src)}#/book/as_of">${esc(dateOf(b.as_of))}</time><span class="b-sep">·</span><span>갱신</span> <time data-source="${esc(S.src)}#/generated_at">${esc(dateOf(p.generated_at))}</time></p>
    <a class="b-expert" id="b-expert" href="${esc(S.modeURL('pro'))}">전문가 모드 <span aria-hidden="true">→</span></a>
  </div></header>`;
}

function renderLead() {
  const p = S.portfolio, h = p.headline || {}, ids = h.evidence_ids || [];
  const chips = ids.map(id => { const e = evidenceOf(id); if (!e) return ''; return `<button type="button" class="b-chip" data-open-evidence="${esc(id)}" data-ledger="portfolio" title="${esc(unfix(e.provenance?.title || e.label))}"><b>${esc(sourceName(e.provenance?.source))}</b><span>${esc(chipLabel(e))}</span></button>`; }).join('');
  return `<section class="b-lead" aria-labelledby="b-verdict">
    <p class="b-eyebrow">오늘의 판단 <span class="b-sep">·</span> <time>${esc(dateOf(p.generated_at))}</time></p>
    <h1 id="b-verdict" class="b-verdict" data-source="${esc(S.src)}#/headline/ko" data-evidence="${esc(ids.join(' '))}" data-ledger="portfolio">${esc(h.ko || '')}</h1>
    <p class="b-verdict-en" lang="en" data-source="${esc(S.src)}#/headline/en">${esc(h.en || '')}</p>
    <div class="b-chips" aria-label="출처">${chips}</div>
    <form class="b-ask" id="b-ask" role="search" autocomplete="off">
      <label class="sr-only" for="b-q">종목·질문·시나리오 입력</label>
      <input id="b-q" name="q" type="text" spellcheck="false" placeholder="종목·질문·시나리오 입력 (예: TSM, &quot;대만 해협 30일 봉쇄면?&quot;)" aria-describedby="b-note" aria-autocomplete="list" aria-controls="b-suggest" aria-expanded="false">
      <kbd aria-hidden="true">/</kbd>
      <button type="submit">분석</button>
      <div class="b-suggest" id="b-suggest" role="listbox" hidden></div>
    </form>
    <p class="b-note" id="b-note" role="status" hidden></p>
  </section>`;
}

function renderGlobe() {
  return `<figure class="b-globe" aria-label="IMF PortWatch 해상 병목 지구본">
    <div class="b-globe-host" id="b-globe-host"><p class="b-globe-off">IMF PortWatch 통항 불러오는 중…</p></div>
    <button type="button" class="b-globe-tag" id="b-globe-tag" hidden></button>
    <figcaption class="b-globe-cap"><span title="입자 1개 = 하루 4척 (양수면 최소 1개) · 항로와 움직임은 도식">점 = 7일 평균 통항 · 옅은 선 = 365일 평균</span><span id="b-globe-asof"></span></figcaption>
  </figure>`;
}

function renderNumbers() {
  const p = S.portfolio, hc = S.hc, ci = (p.channels || []).indexOf(hc), st = p.stress || {}, v = S.validation?.summary;
  const target = hc ? (hc.chokepoint_id || hc.id) : '';
  const a = hc ? `<div class="b-num">
      <h2>실질 노출 비중 차이</h2>
      <p class="b-big">${hc.material_active_pp != null
        ? num(hc.material_active_pp, { sign: true, unit: '%p', src: `${S.src}#/channels/${ci}/material_active_pp`, ev: (hc.evidence_ids || []).join(' '), ledger: 'portfolio' })
        : num(hc.active_pp, { sign: true, unit: '%p', src: `${S.src}#/channels/${ci}/active_pp`, ev: `${p.book?.evidence_id || ''} ${p.benchmark?.evidence_id || ''}`.trim(), ledger: 'portfolio' })}</p>
      <p class="b-expl">${hc.material_active_pp != null ? `보유 ${num(hc.book_material_pct, { unit: '%', src: `${S.src}#/channels/${ci}/book_material_pct`, ledger: 'portfolio' })} vs 벤치마크 ${num(hc.bench_material_pct, { unit: '%', src: `${S.src}#/channels/${ci}/bench_material_pct`, ledger: 'portfolio' })} · ` : '벤치마크 대비, '}${esc(unfix(hc.label_ko) || CHANNELS[hc.id]?.ko || hc.id)} 경로</p>
      <button type="button" class="b-more" data-open-chokepoint="${esc(target)}">근거 <span aria-hidden="true">›</span></button>
    </div>` : '';
  const b = st.book ? `<div class="b-num">
      <h2>시나리오 하위 5% 현금흐름 충격</h2>
      <p class="b-big">${num(st.book.p05, { unit: '%', src: `${S.src}#/stress/book/p05`, ev: st.evidence_id, ledger: 'portfolio' })}<span class="b-bench">벤치마크 ${num(st.bench?.p05, { unit: '%', src: `${S.src}#/stress/bench/p05`, ev: st.evidence_id, ledger: 'portfolio' })}</span></p>
      <p class="b-expl">네 경로 동시 충격 ${num(st.paths, { d: 0, src: `${S.src}#/stress/paths` })}개 경로 중 하위 5%</p>
      <button type="button" class="b-more" data-open-evidence="${esc(st.evidence_id || '')}" data-ledger="portfolio">근거 <span aria-hidden="true">›</span></button>
    </div>` : '';
  const c = v && Number.isFinite(Number(v.events_n)) ? `<div class="b-num">
      <h2>과거 사건 검증</h2>
      <p class="b-big"><span class="num" data-source="${esc(S.vsrc)}#/summary/sign_ok_n" data-value="${raw(v.sign_ok_n, 0)}">${fmt(v.sign_ok_n, 0)}</span><span class="b-of">/</span><span class="num" data-source="${esc(S.vsrc)}#/summary/events_n" data-value="${raw(v.events_n, 0)}">${fmt(v.events_n, 0)}</span><span class="b-big-txt">사건 방향 일치</span></p>
      <p class="b-expl">평균 IC ${num(v.mean_ic, { d: 2, src: `${S.vsrc}#/summary/mean_ic` })} · 음수가 예상 방향 (노출 클수록 손실)</p>
      <button type="button" class="b-more" data-open-validation="1">근거 <span aria-hidden="true">›</span></button>
    </div>` : '';
  const n = [a, b, c].filter(Boolean).length;
  return `<section class="b-numbers b-n${n}" aria-label="핵심 수치">${a}${b}${c}</section>`;
}

function actionRows() {
  const p = S.portfolio;
  return (p.actions || []).map((x, i) => `<tr data-open-ticker="${esc(x.ticker)}" tabindex="0" aria-label="${esc(x.ticker)} 상세 보기">
      <th scope="row"><b class="tk">${esc(x.ticker)}</b><span class="nm">${esc(prettyName(x.name))}</span></th>
      <td class="r">${num(x.weight_pct, { d: 2, src: `${S.src}#/actions/${i}/weight_pct`, ev: p.book?.evidence_id, ledger: 'portfolio' })}</td>
      <td>${chip(x.verdict)}</td>
      <td class="reason">${evidenceText(x.reason_ko, 'portfolio')}</td>
      <td class="r">${num(x.p05_pct, { unit: '%', src: `${S.src}#/actions/${i}/p05_pct`, ev: (x.evidence_ids || []).join(' '), ledger: 'portfolio' })}</td>
    </tr>`).join('');
}

function renderDistribution() {
  const d = S.portfolio.verdict_distribution || {}, total = VERDICT_ORDER.reduce((s, k) => s + (Number(d[k]) || 0), 0);
  if (!total) return '';
  const seg = VERDICT_ORDER.map(k => { const n = Number(d[k]) || 0; return n ? `<span class="seg v-${VERDICT[k].cls}" style="flex:${n}" title="${VERDICT[k].ko} ${n}"></span>` : ''; }).join('');
  const legend = VERDICT_ORDER.map(k => `<li><i class="sw v-${VERDICT[k].cls}" aria-hidden="true"></i>${VERDICT[k].ko} ${num(d[k] || 0, { d: 0, src: `${S.src}#/verdict_distribution/${k}` })}</li>`).join('');
  return `<div class="b-dist"><p class="b-dist-h">전체 <span class="num" data-value="${total}" data-source="${esc(S.src)}#/verdict_distribution">${total}</span>종목 판단 분포</p><div class="b-bar" role="img" aria-label="${VERDICT_ORDER.map(k => `${VERDICT[k].ko} ${d[k] || 0}`).join(', ')}">${seg}</div><ul class="b-dist-legend">${legend}</ul></div>`;
}

const HOLD_COLS = [['ticker', '종목', 0], ['weight_pct', '비중 %', 1], ['bench_weight_pct', '벤치마크 %', 1], ['verdict', '판단', 0], ['taiwan', '대만', 1], ['china_export', '중국 수출통제', 1], ['hormuz', '호르무즈', 1], ['malacca', '말라카', 1], ['p05_pct', '하위 5% %', 1]];
function holdingsTable() {
  const rows = (S.portfolio.holdings || []).map((h, i) => ({ h, i }));
  const { key, desc } = S.sort, val = ({ h }) => CHANNEL_KEYS.includes(key) ? Number(h.chans?.[key] ?? -1) : key === 'verdict' ? VERDICT_ORDER.indexOf(h.verdict) : h[key];
  rows.sort((a, b) => { const x = val(a), y = val(b); const c = typeof x === 'number' && typeof y === 'number' ? x - y : String(x ?? '').localeCompare(String(y ?? '')); return desc ? -c : c; });
  const head = HOLD_COLS.map(([k, label, r]) => `<th scope="col" class="${r ? 'r' : ''}" aria-sort="${S.sort.key === k ? (S.sort.desc ? 'descending' : 'ascending') : 'none'}"><button type="button" data-sort="${k}">${label}${S.sort.key === k ? (S.sort.desc ? ' ↓' : ' ↑') : ''}</button></th>`).join('');
  const body = rows.map(({ h, i }) => `<tr data-open-ticker="${esc(h.ticker)}" tabindex="0"><th scope="row"><b class="tk">${esc(h.ticker)}</b><span class="nm">${esc(prettyName(h.name))}</span></th><td class="r">${num(h.weight_pct, { d: 2, src: `${S.src}#/holdings/${i}/weight_pct` })}</td><td class="r">${num(h.bench_weight_pct, { d: 2, src: `${S.src}#/holdings/${i}/bench_weight_pct` })}</td><td>${chip(h.verdict, { small: true })}</td>${CHANNEL_KEYS.map(k => `<td class="r">${num(h.chans?.[k], { d: 2, src: `${S.src}#/holdings/${i}/chans/${k}` })}</td>`).join('')}<td class="r">${num(h.p05_pct, { unit: '%', src: `${S.src}#/holdings/${i}/p05_pct` })}</td></tr>`).join('');
  return `<div class="b-scroll"><table class="b-table b-hold-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div><p class="b-fine">노출도는 0–1 (공시 근거 기반) · 하위 5%는 공동 시나리오의 종목별 현금흐름 충격 · 행을 누르면 상세가 열립니다.</p>`;
}

function renderActions() {
  const p = S.portfolio, b = p.book || {};
  const n = (p.holdings || []).length;
  return `<section class="b-actions" aria-labelledby="b-actions-h">
    <header class="b-sec-head"><h2 id="b-actions-h">검토가 필요한 보유 종목</h2><p>${esc(unfix(b.name_ko) || '')} · 시나리오 하위 5% 기여와 초과 노출 순</p></header>
    <div class="b-scroll"><table class="b-table b-act-table">
      <thead><tr><th scope="col">종목</th><th scope="col" class="r">비중 %</th><th scope="col">판단</th><th scope="col">사유</th><th scope="col" class="r">하위 5%</th></tr></thead>
      <tbody>${actionRows()}</tbody>
    </table></div>
    ${renderDistribution()}
    ${n ? `<button type="button" class="b-expand" id="b-expand" aria-expanded="${S.holdingsOpen}" aria-controls="b-holdings">${S.holdingsOpen ? '보유 종목 접기' : `전체 보유 종목 보기 (${n})`} <span aria-hidden="true">${S.holdingsOpen ? '‹' : '›'}</span></button>
    <div id="b-holdings" class="b-holdings"${S.holdingsOpen ? '' : ' hidden'}>${S.holdingsOpen ? holdingsTable() : ''}</div>` : ''}
  </section>`;
}

function renderTrust() {
  const p = S.portfolio, v = S.validation;
  const parts = [`13F <time>${esc(dateOf(p.book?.as_of))}</time>`, `벤치마크 <time>${esc(dateOf(p.benchmark?.as_of))}</time>`, `PortWatch <time id="b-trust-pw">${esc(dateOf(S.chokepoints?.as_of || p.chokepoints_as_of || p.headline?.as_of))}</time>`];
  if (v) parts.push(`사건 검증 <time>${esc(dateOf(v.generated_at))}</time>`);
  const versions = [unfix(p.methodology_version), v ? unfix(v.methodology_version) : ''].filter(Boolean).join(' · ');
  const item = html => `<span class="b-ti">${html}</span>`;
  return `<footer class="b-trust"><p>${[...parts.map(item), item('모든 수치는 코드로 계산 · LLM은 서술만, 검증기 통과 문장만 게시'), item(esc(versions)), item(`<a href="${DOCS}METHODOLOGY.md" target="_blank" rel="noopener">방법론 ›</a>`), item(`<a href="${esc(S.modeURL('pro'))}" data-expert="1">전문가 모드 ›</a>`)].join('<span class="b-sep" aria-hidden="true">·</span>')}</p></footer>`;
}

function fixtureBanner() {
  const p = S.portfolio;
  if (!S.mock && !isFixture(p.methodology_version) && !isFixture(p.headline?.ko)) return '';
  return '<div class="b-fixture" role="note"><b>FIXTURE</b> 예시 데이터 · 엔진 산출물이 아닙니다 (?mock=1, public/mock/)</div>';
}

function render() {
  const p = S.portfolio;
  S.hc = headlineChannel(p);
  $('brief').innerHTML = `${fixtureBanner()}${renderTop()}<main class="b-wrap b-main" id="b-main">${renderLead()}${renderGlobe()}${renderNumbers()}${renderActions()}${renderTrust()}</main><div class="b-print" id="b-print" aria-hidden="true"></div>`;
  if (matchMedia('(max-width: 640px)').matches) $('b-q')?.setAttribute('placeholder', '종목·질문·시나리오 (예: TSM)');
  paintGlobeTag();
}

// ----------------------------------------------------------------------------- globe (lazy)
function focusChokepoint() {
  const items = S.chokepoints?.items || [];
  const id = S.hc?.chokepoint_id || CHANNELS[S.hc?.id]?.cp;
  return items.find(x => x.id === id) || anomaly(items);
}
function paintGlobeTag() {
  const tag = $('b-globe-tag'), items = S.chokepoints?.items || [];
  if (!tag || !items.length) return;
  const x = focusChokepoint(); if (!x) return;
  const i = items.indexOf(x), src = `${S.chokeSrc}#/items/${i}`;
  tag.hidden = false; tag.dataset.openChokepoint = x.id;
  tag.innerHTML = `<span>${esc(koChokepoint(x.name))}</span>${num(x.delta_pct, { sign: true, unit: '%', src: `${src}/delta_pct`, cls: 'tag-delta' })}<small>7일 평균 ${num(x.mean_7d, { src: `${src}/mean_7d` })}척/일 · 365일 ${num(x.baseline_365d, { src: `${src}/baseline_365d` })}척</small>`;
  const asof = $('b-globe-asof'); if (asof) asof.textContent = `IMF PortWatch ${dateOf(S.chokepoints.as_of)}`;
  const pw = $('b-trust-pw'); if (pw) pw.textContent = dateOf(S.chokepoints.as_of);
}
let globe = null, globeToken = 0;
async function mountGlobe() {
  const host = $('b-globe-host'); if (!host || !S.chokepoints) return;
  const token = ++globeToken;
  try {
    globe?.destroy?.(); globe = null;
    const module = await import('./globe.js');
    if (token !== globeToken || !document.body.contains(host)) return;
    const still = matchMedia('(prefers-reduced-motion: reduce)').matches;
    const inst = await module.mountGlobe(host, { chokepoints: S.chokepoints, exposure: null }, { focus: focusChokepoint(), still, onChokepoint: c => S.drawerUI.openChokepoint(c.id) });
    if (token !== globeToken) { inst.destroy?.(); return; }
    globe = inst;
  } catch (error) {
    console.warn(`globe: ${error.message}`);
    host.innerHTML = '<p class="b-globe-off">지구본을 표시할 수 없습니다 · 아래 수치는 그대로 유효합니다</p>';
  }
}

// ----------------------------------------------------------------------------- input
function knownTickers() {
  const m = new Map();
  for (const h of S.portfolio.holdings || []) m.set(h.ticker, { ticker: h.ticker, name: prettyName(h.name), weight: h.weight_pct });
  for (const x of S.index?.items || []) if (!m.has(x.ticker)) m.set(x.ticker, { ticker: x.ticker, name: prettyName(x.name), weight: null });
  return m;
}
function closestTicker(q) {
  const known = knownTickers(), text = String(q);
  for (const tok of text.match(/[A-Za-z][A-Za-z.\-]{0,5}/g) || []) { const t = tok.toUpperCase(); if (t.length >= 2 && known.has(t)) return { ...known.get(t), why: '입력에 포함된 종목' }; }
  for (const [ko, t] of Object.entries(KO_NAMES)) if (text.includes(ko) && known.has(t)) return { ...known.get(t), why: '종목명 일치' };
  const low = text.toLowerCase();
  for (const x of known.values()) { const first = unfix(x.name).toLowerCase().split(/[\s,]+/)[0]; if (first.length >= 4 && low.includes(first)) return { ...x, why: '종목명 일치' }; }
  for (const [re, ch] of CHANNEL_WORDS) if (re.test(text)) {
    const c = (S.portfolio.channels || []).find(x => x.id === ch), top = c?.top?.[0];
    if (top) return { ticker: top.ticker, name: prettyName(top.name), weight: top.weight_pct, why: `${unfix(c.label_ko) || CHANNELS[ch].ko} 경로 최대 기여 종목` };
  }
  return null;
}
function note(html) { const el = $('b-note'); if (!el) return; el.innerHTML = html; el.hidden = !html; }
function suggestions(q) {
  const box = $('b-suggest'), input = $('b-q'); if (!box) return;
  const s = q.trim().toUpperCase();
  if (!s || s.includes(' ') || s.length > 12) { box.hidden = true; input?.setAttribute('aria-expanded', 'false'); return; }
  const list = [...knownTickers().values()].filter(x => x.ticker.startsWith(s) || (s.length >= 3 && unfix(x.name).toUpperCase().includes(s))).sort((a, b) => (b.ticker === s) - (a.ticker === s) || (b.weight ?? -1) - (a.weight ?? -1)).slice(0, 6);
  S.suggest = { list, i: -1 };
  box.innerHTML = list.map((x, i) => `<button type="button" role="option" id="b-sg-${i}" data-open-ticker="${esc(x.ticker)}"><b>${esc(x.ticker)}</b><span>${esc(x.name)}</span>${x.weight != null ? `<em>비중 ${fmt(x.weight, 2)}%</em>` : '<em>커버리지</em>'}</button>`).join('');
  box.hidden = !list.length; input?.setAttribute('aria-expanded', String(!!list.length));
}
function submit(raw) {
  const q = raw.trim(); if (!q) return;
  $('b-suggest').hidden = true;
  if (/^[A-Za-z][A-Za-z.\-]{0,6}$/.test(q)) { note(''); S.drawerUI.openHolding(q.toUpperCase()); return; }
  const match = closestTicker(q);
  if (S.mode === 'LIVE') {
    if (match) { note(''); S.drawerUI.openHolding(match.ticker, { tab: 'act', question: q }); }
    else note('질문과 함께 종목을 입력하세요 · 예: <code>TSM, 대만 해협 30일 봉쇄면?</code>');
    return;
  }
  note(`자유 질문은 실시간 엔진에서 에이전트가 실행합니다 (현재 정적 스냅숏).${match ? ` 가장 가까운 종목: <button type="button" class="b-link" data-open-ticker="${esc(match.ticker)}" data-tab="whatif"><b>${esc(match.ticker)}</b> ${esc(match.name)} ›</button> <span class="b-why">${esc(match.why)}</span>` : ''}`);
}

// ----------------------------------------------------------------------------- book switch, holdings
async function switchBook(book) {
  if (book === S.book) return;
  let entry = S.books[book];
  if (!entry) {
    const url = S.mock ? `./mock/portfolio_${book}.json` : `${S.base}/portfolio/${book}.json`;
    try { const r = await fetch(url, { cache: 'no-cache' }); if (r.ok) entry = S.books[book] = { portfolio: await r.json(), url }; } catch { /* reported below */ }
  }
  if (!entry) { note('이 포트폴리오 파일은 아직 생성되지 않았습니다.'); return; }
  S.book = book; S.portfolio = entry.portfolio; S.src = srcOf(entry.url); S.holdingsOpen = false;
  const p = new URLSearchParams(location.search); if (book === 'kic13f') p.delete('book'); else p.set('book', book);
  history.replaceState(null, '', `${location.pathname}${p.toString() ? `?${p}` : ''}`);
  globe?.destroy?.(); globe = null;
  render(); mountGlobe();
}

function bind() {
  const root = $('brief');
  root.addEventListener('click', e => {
    const el = e.target.closest('[data-open-evidence],[data-open-ticker],[data-open-chokepoint],[data-open-validation],[data-book],[data-sort],#b-expand,[data-expert]');
    if (!el || !root.contains(el)) { if (!e.target.closest('.b-ask')) $('b-suggest')?.setAttribute('hidden', ''); return; }
    if (el.dataset.expert || el.id === 'b-expert') { el.href = S.modeURL('pro'); return; }
    e.preventDefault();
    if (el.dataset.openEvidence) S.drawerUI.openEvidence(el.dataset.ledger || 'portfolio', el.dataset.openEvidence);
    else if (el.dataset.openTicker) { $('b-suggest').hidden = true; S.drawerUI.openHolding(el.dataset.openTicker, { tab: el.dataset.tab }); }
    else if (el.dataset.openChokepoint) S.drawerUI.openChokepoint(el.dataset.openChokepoint);
    else if (el.dataset.openValidation) S.drawerUI.openValidation();
    else if (el.dataset.book) switchBook(el.dataset.book);
    else if (el.dataset.sort) { const k = el.dataset.sort; S.sort = { key: k, desc: S.sort.key === k ? !S.sort.desc : !['ticker', 'verdict', 'p05_pct'].includes(k) }; $('b-holdings').innerHTML = holdingsTable(); $('b-holdings').querySelector(`[data-sort="${k}"]`)?.focus(); }
    else if (el.id === 'b-expand') { S.holdingsOpen = !S.holdingsOpen; const box = $('b-holdings'); box.hidden = !S.holdingsOpen; box.innerHTML = S.holdingsOpen ? holdingsTable() : ''; el.setAttribute('aria-expanded', String(S.holdingsOpen)); el.innerHTML = S.holdingsOpen ? '보유 종목 접기 <span aria-hidden="true">‹</span>' : `전체 보유 종목 보기 (${(S.portfolio.holdings || []).length}) <span aria-hidden="true">›</span>`; }
  });
  root.addEventListener('submit', e => { if (e.target.id === 'b-ask') { e.preventDefault(); const sg = S.suggest; if (sg && sg.i >= 0 && sg.list[sg.i] && !$('b-suggest').hidden) { $('b-suggest').hidden = true; S.drawerUI.openHolding(sg.list[sg.i].ticker); return; } submit($('b-q').value); } });
  root.addEventListener('input', e => { if (e.target.id === 'b-q') { suggestions(e.target.value); if (!e.target.value) note(''); } });
  root.addEventListener('keydown', e => {
    if (e.target.id === 'b-q' && S.suggest && !$('b-suggest').hidden && ['ArrowDown', 'ArrowUp'].includes(e.key)) {
      e.preventDefault(); const n = S.suggest.list.length; S.suggest.i = (S.suggest.i + (e.key === 'ArrowDown' ? 1 : -1) + n) % n;
      $('b-suggest').querySelectorAll('button').forEach((b, i) => b.setAttribute('aria-selected', String(i === S.suggest.i)));
      e.target.setAttribute('aria-activedescendant', `b-sg-${S.suggest.i}`); return;
    }
    if ((e.key === 'Enter' || e.key === ' ') && e.target.matches('tr[data-open-ticker]')) { e.preventDefault(); S.drawerUI.openHolding(e.target.dataset.openTicker); }
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') { if (S.drawerUI.isOpen()) { e.preventDefault(); S.drawerUI.close(); } else $('b-suggest')?.setAttribute('hidden', ''); return; }
    if (e.key === '/' && !e.metaKey && !e.ctrlKey && !['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName) && !S.drawerUI.isOpen()) { e.preventDefault(); $('b-q')?.focus(); }
  });
}

// ----------------------------------------------------------------------------- data after first paint
async function afterPaint() {
  const chokeURL = `${S.base}/chokepoints.json`;
  const [choke, index] = await Promise.all([
    fetch(chokeURL).then(r => (r.ok ? r.json() : null)).catch(() => null),
    S.snap.index().catch(() => null),
  ]);
  S.index = index;
  const useChokepoints = (data, src) => { S.chokepoints = data; S.chokeSrc = src; paintGlobeTag(); S.drawerUI.refresh('chokepoints'); mountGlobe(); };
  const snapshotOK = choke?.items?.length > 0;
  if (snapshotOK) useChokepoints({ ...choke, source: 'snapshot' }, srcOf(chokeURL));
  try {
    const api = await import('./api.js');
    await api.ready; S.api = api; S.mode = api.mode;
    // LIVE: the engine's chokepoints. Snapshot file empty: the same path the terminal uses
    // (browser PortWatch query in SNAPSHOT mode, captured PortWatch response in MOCK mode).
    if (api.mode === 'LIVE' || !snapshotOK) {
      const late = data => { if (data?.items?.length) useChokepoints(data, data.source === 'live' ? 'portwatch.imf.org (browser query)' : srcOf(chokeURL)); };
      const off = api.onLiveChokepoints?.(late);
      const data = await api.getJSON('chokepoints', 'chokepoints');
      S.livePending = !!data?.live_pending;
      if (data?.items?.length) { off?.(); useChokepoints({ ...data, source: data.source || (api.mode === 'LIVE' ? 'live' : api.mode === 'MOCK' ? 'mock' : 'snapshot') }, api.mode === 'LIVE' ? 'api/v1/chokepoints' : api.mode === 'MOCK' ? 'mock/chokepoints.json' : data.source === 'live' ? 'portwatch.imf.org (browser query)' : srcOf(chokeURL)); }
    }
  } catch (error) { console.warn(`chokepoints: ${error.message}`); }
  if (S.livePending) setTimeout(() => { if (!S.chokepoints?.items?.length) { S.livePending = false; const h = $('b-globe-host'); if (h) h.innerHTML = '<p class="b-globe-off">PortWatch 통항 자료를 불러오지 못했습니다 · 아래 수치는 엔진 산출 시점 자료입니다</p>'; } }, 47000);
  if (!S.chokepoints?.items?.length) { const host = $('b-globe-host'); if (host) host.innerHTML = `<p class="b-globe-off">${S.livePending ? 'IMF PortWatch 실시간 조회 중… 도착하면 지구본이 표시됩니다' : 'PortWatch 통항 자료를 불러오지 못했습니다 · 아래 수치는 엔진 산출 시점 자료입니다'}</p>`; }
}

export function mountBrief(opts) {
  S = {
    ...opts, src: srcOf(opts.portfolioURL), vsrc: srcOf(opts.validationURL), snap: createSnapshotApi({ snapshotBase: opts.base }),
    chokepoints: null, chokeSrc: '', index: null, api: null, mode: opts.mock ? 'MOCK' : 'SNAPSHOT', holdingsOpen: false,
    sort: { key: 'weight_pct', desc: true }, books: { [opts.book]: { portfolio: opts.portfolio, url: opts.portfolioURL } }, suggest: null,
  };
  document.documentElement.lang = 'ko';
  document.title = 'Geo-Alpha Sentinel · 오늘의 판단';
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', '#f7f3ea');
  S.drawerUI = createDrawer(S, { prettyName, headlineChannel });
  render();
  bind();
  const params = new URLSearchParams(location.search);
  if (params.get('t')) S.drawerUI.openHolding(params.get('t').toUpperCase(), { tab: params.get('tab'), fromURL: true });
  else if (params.get('c')) S.drawerUI.openChokepoint(params.get('c'), { tab: params.get('tab'), fromURL: true });
  requestAnimationFrame(() => setTimeout(afterPaint, 0));
}
