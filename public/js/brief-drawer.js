// L1 detail drawer for the Brief: holding (?t=) and chokepoint/channel (?c=) with four fixed tabs
// 왜 · 얼마나 확실한가 · 만약 · 대응, plus evidence records and the event-validation table.
import {
  esc, num, fmt, raw, dateOf, stamp, chip, VERDICT, CHANNELS, CHANNEL_KEYS, METRICS, SOURCES, sourceName, unfix,
  koChokepoint, evidenceText, lineChart, histogram,
} from './brief-format.js';
import { renderRiskWiring, drawEvidenceWires } from './evidence.js';
import { renderMEMO } from './panels.js';
import { runStressReplica, recomputeScenarioMetrics, DEFAULT_SCENARIO } from './stress_replica.js';

const TABS = [['why', '왜'], ['sure', '얼마나 확실한가'], ['whatif', '만약'], ['act', '대응']];
const PRO_FN = { why: 'RISK', sure: 'AGNT', whatif: 'STRS', act: 'MEMO' };
const DOCS = 'https://github.com/cjw0076/geo-alpha-sentinel/blob/main/docs/';
const TOOLS = {
  'sec.company_profile': 'SEC 기업 프로필·재무', 'sec.risk_factors': '공시 위험 요인 원문', 'exposure.tag_4w': '위험 문장 태깅', 'usgs.minerals': 'USGS 광물 집중도',
  'portwatch.chokepoints': 'PortWatch 해상 통항', 'csl.screen': '제재 명단 대조', 'comtrade.import_dependency': 'UN Comtrade 수입 의존', 'sandbox.derived_fundamentals': '재무 파생치 계산',
  'sandbox.stress_mc': '몬테카를로 스트레스', 'sandbox.run': '샌드박스 계산', 'memo.draft': '메모 초안', 'verify.claims': '주장 검증', 'graph.build': '공급망 그래프', 'graph.triples': '공급망 그래프',
};
const CHANNEL_CHOKEPOINT = { taiwan: 'Taiwan Strait', hormuz: 'Strait of Hormuz', malacca: 'Malacca Strait' };
const ASIA_ORDER = ['Japan', 'Korea', 'Malaysia', 'Vietnam', 'India', 'Hong Kong', 'Singapore', 'China', 'Taiwan'];
const RULES = [
  ['REDUCE', '시나리오 충격 SRS ≥ 60, 또는 병목 의존도 CPI ≥ 60 이면서 지정학 취약도 GPI ≥ 60'],
  ['HEDGE', 'SRS ≥ 40, 또는 GPI ≥ 55, 또는 CPI ≥ 55'],
  ['ACCUMULATE', '우호국 전환 여력 FAR 점수 ≥ 65'],
  ['HOLD', '그 외'],
];

/** The policy rule of sentinel/agent/runner.py `_recommendation` (display only). */
export function policy(metrics) {
  const v = c => Number((metrics || []).find(m => m.code === c)?.value);
  const cpi = v('CPI'), gpi = v('GPI'), srs = v('SRS'), far = v('FAR');
  if (![cpi, gpi, srs, far].every(Number.isFinite)) return null;
  if (srs >= 60) return { code: 'REDUCE', rule: 0, why: [['SRS', srs, '≥ 60']] };
  if (cpi >= 60 && gpi >= 60) return { code: 'REDUCE', rule: 0, why: [['CPI', cpi, '≥ 60'], ['GPI', gpi, '≥ 60']] };
  if (srs >= 40) return { code: 'HEDGE', rule: 1, why: [['SRS', srs, '≥ 40']] };
  if (gpi >= 55) return { code: 'HEDGE', rule: 1, why: [['GPI', gpi, '≥ 55']] };
  if (cpi >= 55) return { code: 'HEDGE', rule: 1, why: [['CPI', cpi, '≥ 55']] };
  if (far >= 65) return { code: 'ACCUMULATE', rule: 2, why: [['FAR', far, '≥ 65']] };
  return { code: 'HOLD', rule: 3, why: [['SRS', srs, '< 40'], ['FAR', far, '< 65']] };
}

export function createDrawer(S, { prettyName }) {
  const scrim = document.createElement('div');
  scrim.className = 'b-scrim'; scrim.hidden = true;
  const aside = document.createElement('aside');
  aside.className = 'b-drawer'; aside.hidden = true; aside.tabIndex = -1;
  aside.setAttribute('role', 'dialog'); aside.setAttribute('aria-modal', 'true'); aside.setAttribute('aria-labelledby', 'dr-title');
  document.body.append(scrim, aside);

  const st = { cur: null, stack: [], opener: null, analyses: new Map(), live: new Map(), scen: new Map(), rev: new Map(), minerals: null, revToken: 0, stressTimer: 0, revTimer: 0 };
  const isOpen = () => !aside.hidden;
  const ledgerA = T => `a:${T}`;
  const aSrc = T => `${S.base.replace(/^\.\//, '')}/${T}/analysis.json`;

  // ------------------------------------------------------------------- data
  function analysis(T) {
    if (!st.analyses.has(T)) st.analyses.set(T, S.snap.analysis(T).catch(() => null));
    return st.analyses.get(T);
  }
  function holdingRow(T) { const hs = S.portfolio.holdings || []; const i = hs.findIndex(h => h.ticker === T); return i < 0 ? { row: null, i: -1 } : { row: hs[i], i }; }
  function evMap(a) { return new Map((a?.evidence || []).map(e => [e.id, e])); }
  function findEv(a, pred) { return (a?.evidence || []).find(pred) || null; }
  function ledgerRecords(ledger) {
    if (ledger === 'portfolio') return Promise.resolve(S.portfolio.evidence || []);
    if (ledger.startsWith('a:')) return analysis(ledger.slice(2)).then(a => a?.evidence || []);
    return Promise.resolve([]);
  }
  async function loadMinerals() {
    if (!st.minerals) st.minerals = fetch(`${S.base}/minerals.json`).then(r => (r.ok ? r.json() : null)).catch(() => null);
    return st.minerals;
  }

  // ------------------------------------------------------------------- shell
  function syncURL() {
    const p = new URLSearchParams(location.search);
    p.delete('t'); p.delete('c'); p.delete('tab');
    const c = st.stack[0] || st.cur;
    if (isOpen() && c?.kind === 'holding') p.set('t', c.id);
    if (isOpen() && c?.kind === 'chokepoint') p.set('c', c.id);
    if (isOpen() && (c?.kind === 'holding' || c?.kind === 'chokepoint') && c.tab && c.tab !== 'why') p.set('tab', c.tab);
    history.replaceState(null, '', `${location.pathname}${p.toString() ? `?${p}` : ''}`);
    const expert = document.getElementById('b-expert'); if (expert) expert.href = S.modeURL('pro', PRO_FN[c?.tab] && c?.kind === 'holding' ? { f: PRO_FN[c.tab] } : {});
  }
  function show() {
    if (!isOpen()) { st.opener = document.activeElement; aside.hidden = false; scrim.hidden = false; document.body.classList.add('b-locked'); requestAnimationFrame(() => aside.classList.add('is-open')); }
  }
  function close() {
    aside.classList.remove('is-open'); aside.hidden = true; scrim.hidden = true; document.body.classList.remove('b-locked');
    st.cur = null; st.stack = []; syncURL();
    if (st.opener && document.body.contains(st.opener)) st.opener.focus({ preventScroll: true });
  }
  function head({ eyebrow, title, sub = '', meta = '', back = false }) {
    return `<header class="dr-head">${back ? '<button type="button" class="dr-back" data-dr-back="1">← 돌아가기</button>' : ''}<div class="dr-title"><p class="dr-eyebrow">${eyebrow}</p><h2 id="dr-title">${title}${sub ? ` <span class="dr-sub">${sub}</span>` : ''}</h2>${meta ? `<div class="dr-meta">${meta}</div>` : ''}</div><button type="button" class="dr-close" data-dr-close="1" aria-label="닫기 (Esc)">×</button></header>`;
  }
  function tabs(active) {
    return `<div class="dr-tabs" role="tablist" aria-label="상세 보기">${TABS.map(([k, label]) => `<button type="button" role="tab" id="dr-tab-${k}" data-dr-tab="${k}" aria-selected="${k === active}" aria-controls="dr-panel" tabindex="${k === active ? 0 : -1}">${label}</button>`).join('')}</div>`;
  }
  function frame(headHTML, bodyHTML, tab) {
    aside.innerHTML = `${headHTML}${tab ? tabs(tab) : ''}<div class="dr-body" id="dr-panel" ${tab ? `role="tabpanel" aria-labelledby="dr-tab-${tab}"` : ''} tabindex="0">${bodyHTML}</div>`;
  }
  const loading = '<p class="dr-loading">불러오는 중…</p>';
  const sec = (title, body, small = '') => `<section class="dr-sec"><h3>${title}${small ? ` <small>${small}</small>` : ''}</h3>${body}</section>`;

  // ------------------------------------------------------------------- holding
  async function renderHolding() {
    const c = st.cur, T = c.id, { row, i } = holdingRow(T);
    const book = S.portfolio.book || {};
    const run = st.live.get(T);
    const a0 = st.analyses.has(T) ? null : undefined;
    const metaRow = row ? `${chip(row.verdict)}<span>비중 ${num(row.weight_pct, { d: 2, unit: '%', src: `${S.src}#/holdings/${i}/weight_pct` })}</span><span>벤치마크 ${num(row.bench_weight_pct, { d: 2, unit: '%', src: `${S.src}#/holdings/${i}/bench_weight_pct` })}</span><span>하위 5% ${num(row.p05_pct, { unit: '%', src: `${S.src}#/holdings/${i}/p05_pct` })}</span>` : '';
    const eyebrow = row ? `보유 종목 · ${esc(unfix(book.name_ko) || '')}` : '종목';
    const draw = (a, body) => {
      const name = row?.name || a?.company?.name || (S.index?.items || []).find(x => x.ticker === T)?.name || '';
      const live = run?.running ? `<span class="dr-live">에이전트 실행 중 · ${run.events.length}단계</span>` : '';
      frame(head({ eyebrow, title: `<span class="tk">${esc(T)}</span>`, sub: esc(prettyName(name)), meta: `${metaRow}${live}` }), body, c.tab);
    };
    if (a0 === undefined) draw(null, loading);
    const a = await analysis(T);
    if (st.cur !== c) return;
    let body;
    if (c.tab === 'why') body = whyTab(T, a, row, i);
    else if (c.tab === 'sure') body = sureTab(T, a, row);
    else if (c.tab === 'whatif') body = whatifTab(T, a, row, i);
    else body = actTab(T, a, row);
    draw(a, body);
    if (c.tab === 'whatif' && a) { computeWhatIf(T, a, true); }
  }

  function noAnalysis(T, row, i) {
    const live = S.mode === 'LIVE';
    const chans = row?.chans;
    const bars = chans ? CHANNEL_KEYS.map(k => `<li><span>${CHANNELS[k].ko}</span>${bar(chans[k])}${num(chans[k], { d: 2, src: `${S.src}#/holdings/${i}/chans/${k}` })}</li>`).join('') : '';
    return `${bars ? sec('경로별 노출도', `<ul class="dr-bars">${bars}</ul>`, '0–1 · 포트폴리오 계산') : ''}<p class="dr-note">${esc(T)}의 공시 문장·지표·시나리오 입력은 커버리지 40종목 스냅숏에 없습니다.${live ? '' : ' 실시간 엔진에서는 SEC 등록 기업 누구나 분석할 수 있습니다.'}</p>${live ? `<p><button type="button" class="btn" data-dr-run="${esc(T)}">실시간 분석 실행</button></p>` : ''}`;
  }
  const bar = v => `<i class="dr-bar" aria-hidden="true"><s style="width:${Math.max(0, Math.min(100, Number(v) * 100 || 0)).toFixed(1)}%"></s></i>`;

  function channelEvidence(a, k) {
    const key = { taiwan: '대만', china_export: '중국', hormuz: '호르무즈', malacca: '말라카' }[k];
    return findEv(a, e => /^Channel exposure/.test(e.label || '') && (e.label || '').includes(key));
  }
  function filingQuote(a, k) {
    const ex = a.exposure || {}, geos = ex.geographies || [], cats = ex.categories || {};
    const geo = region => { const g = geos.find(x => x.region === region); if (!g) return null; const e = findEv(a, x => (x.label || '').startsWith(`Mentions of ${region} `)); return { text: g.sample_quote, url: g.url, ev: e?.id, why: `위험 요인에서 ${region} ${g.mentions}회 언급`, n: g.mentions, evN: e?.id }; };
    const hit = (cat, re) => { const top = cats[cat]?.top || []; const h = top.find(x => re.test(x.term || '')) || null; return h ? { text: h.quote, url: h.url, ev: h.evidence_id, why: `위험 요인 문장 · ${h.term}` } : null; };
    if (k === 'taiwan') return geo('Taiwan') || hit('weaponized', /taiwan|geopolit/i);
    if (k === 'china_export') return hit('weaponized', /export control/i) || geo('China') || hit('weaponized', /./);
    if (k === 'hormuz') return geo('Middle East') || hit('watts', /./) || hit('weaponized', /sanction|tariff/i);
    for (const r of ASIA_ORDER) { const q = geo(r); if (q) return q; }
    return null;
  }
  function channelRecord(a, k, T) {
    if (k === 'china_export') {
      const ms = (a.minerals || []).filter(m => m.china_share_pct != null).sort((x, y) => y.china_share_pct - x.china_share_pct);
      const m = ms[0]; if (!m) return '';
      const e = findEv(a, x => (x.label || '').startsWith(`${m.name}: China share`)), mi = (a.minerals || []).indexOf(m);
      return `<p class="dr-rec"><b>USGS MCS ${esc(m.year || '')}</b> ${esc(m.name)} 세계 생산 중 중국 ${num(m.china_share_pct, { unit: '%', src: `${aSrc(T)}#/minerals/${mi}/china_share_pct`, ev: e?.id, ledger: ledgerA(T) })}${e ? refBtn(e.id, ledgerA(T)) : ''}</p>`;
    }
    const name = CHANNEL_CHOKEPOINT[k], own = (a.chokepoints || []).length > 0, items = own ? a.chokepoints : S.chokepoints?.items || [];
    const ci = items.findIndex(x => x.name === name), x = items[ci];
    if (!x) return '';
    const e = own ? findEv(a, y => (y.label || '').startsWith(`${name}: 7-day mean transits vs 365-day baseline`)) : null;
    const s = own ? `${aSrc(T)}#/chokepoints/${ci}` : `${S.chokeSrc}#/items/${ci}`;
    return `<p class="dr-rec"><b>IMF PortWatch</b> ${esc(koChokepoint(name))} 7일 평균 ${num(x.mean_7d, { src: `${s}/mean_7d` })}척/일 · 365일 대비 ${num(x.delta_pct, { sign: true, unit: '%', src: `${s}/delta_pct`, ev: e?.id, ledger: ledgerA(T) })} <span class="dr-dim">(${esc(dateOf(x.latest?.date))})</span>${e ? refBtn(e.id, ledgerA(T)) : ''} <button type="button" class="b-link" data-dr-choke="${esc(x.id)}">통항 추이 ›</button></p>`;
  }
  const refBtn = (id, ledger) => `<button type="button" class="ev-ref" data-open-evidence="${esc(id)}" data-ledger="${esc(ledger)}" aria-label="근거 ${esc(id)}">${esc(id)}</button>`;

  function whyTab(T, a, row, i) {
    if (!a) return noAnalysis(T, row, i);
    const chans = a.engine?.exposure_by_channel || a.stress?.inputs?.exposure || {};
    const top = CHANNEL_KEYS.filter(k => chans[k] != null).sort((x, y) => chans[y] - chans[x]).slice(0, 3);
    const filing = a.exposure?.filing || a.company?.latest_annual || {};
    const blocks = top.map(k => {
      const e = channelEvidence(a, k), q = filingQuote(a, k);
      return `<article class="dr-chan">
        <header><h4>${CHANNELS[k].ko}</h4>${bar(chans[k])}${num(chans[k], { d: 2, src: `${aSrc(T)}#/engine/exposure_by_channel/${k}`, ev: e?.id, ledger: ledgerA(T), cls: 'dr-chan-v' })}</header>
        ${q ? `<blockquote><p>“${esc(q.text)}”</p><footer><a href="${esc(q.url)}" target="_blank" rel="noopener">${esc(filing.form || '공시')} ${esc(filing.filed || '')} 원문 ↗</a><span class="dr-dim">${esc(q.why)}</span>${q.ev ? refBtn(q.ev, ledgerA(T)) : ''}</footer></blockquote>` : '<p class="dr-dim">이 경로를 직접 언급한 공시 문장은 없습니다 (업종·지역 신호로 계산).</p>'}
        ${channelRecord(a, k, T)}
      </article>`;
    }).join('');
    const ms = ['CPI', 'GPI', 'SRS', 'FAR'].map(code => {
      const m = (a.metrics || []).find(x => x.code === code); if (!m) return '';
      const mi = a.metrics.indexOf(m), e = findEv(a, x => (x.label || '').startsWith(`${code} —`));
      const val = code === 'FAR' ? `<span class="m-rating">${esc(m.rating || '')}</span>${num(m.value, { src: `${aSrc(T)}#/metrics/${mi}/value`, ev: e?.id, ledger: ledgerA(T) })}` : num(m.value, { src: `${aSrc(T)}#/metrics/${mi}/value`, ev: e?.id, ledger: ledgerA(T) });
      return `<details class="dr-metric" data-metric="${code}"><summary><span class="m-name"><b>${METRICS[code].ko}</b> <abbr title="${esc(m.name)}">${code}</abbr></span><span class="m-val">${val}</span><span class="m-desc">${METRICS[code].desc}</span><span class="m-open" aria-hidden="true">계산 근거</span></summary><div class="m-wire" data-wire="${code}"></div></details>`;
    }).join('');
    return `${sec('주요 노출 경로', blocks, '공시 근거 기반 노출도 0–1 · 상위 3개')}${sec('네 가지 지표', `<div class="dr-metrics">${ms}</div>`, '0–100 · 펼치면 원문 → 구성요소 → 지표 연결')}`;
  }

  // ------------------------------------------------------------------- how sure
  function decisions(T, events, a) {
    const src = `${aSrc(T)}#/events`;
    const at = e => events.indexOf(e);
    const plan = events.find(e => e.type === 'plan');
    const steps = plan?.data?.steps || [], hyp = plan?.data?.hypotheses || [];
    const calls = [];
    for (const e of events) if (e.type === 'tool_call') { const t = e.data?.tool || String(e.title).split('(')[0]; if (!calls.includes(t)) calls.push(t); }
    const used = new Set([...calls, ...events.filter(e => e.type === 'tool_result').map(e => e.data?.tool)]);
    const skipped = steps.map(s => s.tool).filter(t => !used.has(t));
    const replans = events.filter(e => e.type === 'replan');
    const verifs = events.filter(e => e.type === 'verification');
    const done = events.find(e => e.type === 'done');
    const finalMemo = [...events].reverse().find(e => e.type === 'memo');
    const engine = done?.data?.engine || a?.engine || {};
    const li = (h, body, e) => `<li><b>${h}</b><span>${body}</span>${e ? `<span class="dr-seq" data-source="${esc(src)}/${at(e)}">#${esc(e.seq ?? at(e) + 1)}</span>` : ''}</li>`;
    const out = [];
    if (plan) out.push(li('계획', `${steps.length}단계 계획 · 검증할 가설 ${hyp.length}개${hyp.length ? `<ul class="dr-hyp">${hyp.map(h => `<li lang="en">${esc(h)}</li>`).join('')}</ul>` : ''}`, plan));
    if (calls.length) out.push(li('고른 조사 도구', calls.map(t => `<span class="dr-tool" title="${esc(t)}">${esc(TOOLS[t] || t)}</span>`).join(''), events.find(e => e.type === 'tool_call')));
    if (plan) out.push(li('제외한 단계', skipped.length ? skipped.map(t => esc(TOOLS[t] || t)).join(' · ') : '계획 대비 생략 없음'));
    out.push(li('재계획', replans.length ? replans.map(e => esc(e.title)).join(' · ') : '0회 · 첫 계획으로 완료', replans[0]));
    for (const v of verifs) {
      const d = v.data || {}, rej = (d.rejected || []).length, bear = /bear/i.test(v.title || '');
      out.push(li(bear ? '반대 논거 검증' : '주장 검증', bear ? `반대 논거 ${fmt(d.checked, 0)}건 중 ${fmt((d.checked || 0) - rej, 0)}건 근거 확인` : `${fmt(d.checked, 0)}건 확인 · 기각 ${fmt(rej, 0)}건`, v));
    }
    if (done) out.push(li('종료 조건', `검증 통과 → 최종 메모 확정${finalMemo?.data?.recommendation ? ` (${esc(VERDICT[finalMemo.data.recommendation]?.ko || finalMemo.data.recommendation)})` : ''} · ${fmt((done.data?.ms || 0) / 1000, 1)}초 · LLM 호출 ${fmt(engine.llm_calls || 0, 0)}회${engine.llm && engine.llm !== 'deterministic' ? ` (${esc(engine.llm)})` : ' (결정론적 엔진)'}`, done));
    return out.length ? `<ol class="dr-steps">${out.join('')}</ol>` : '<p class="dr-dim">기록된 에이전트 이벤트가 없습니다.</p>';
  }
  function sureTab(T, a, row) {
    const run = st.live.get(T);
    const parts = [];
    if (a) {
      const groups = new Map();
      for (const e of a.evidence || []) { const p = e.provenance || {}; const g = groups.get(p.source) || { n: 0, at: '' }; g.n++; if ((p.retrieved_at || '') > g.at) g.at = p.retrieved_at || ''; groups.set(p.source, g); }
      const order = Object.keys(SOURCES);
      const rows = [...groups].sort((x, y) => order.indexOf(x[0]) - order.indexOf(y[0])).map(([s, g]) => `<tr><th scope="row">${esc(sourceName(s))}<small>${esc(SOURCES[s]?.[1] || '')}</small></th><td class="r"><span class="num" data-value="${g.n}" data-source="${esc(aSrc(T))}#/evidence">${g.n}</span></td><td class="r">${esc(stamp(g.at))}</td></tr>`).join('');
      const f = a.exposure?.filing || a.company?.latest_annual;
      parts.push(sec('데이터 기준', `<table class="dr-table"><thead><tr><th scope="col">출처</th><th scope="col" class="r">근거 수</th><th scope="col" class="r">수집 시각</th></tr></thead><tbody>${rows}</tbody></table>${f ? `<p class="dr-fine">분석에 쓴 공시: <a href="${esc(f.url)}" target="_blank" rel="noopener">${esc(f.form)} · ${esc(f.filed)} 제출 · 회계기간 ${esc(f.period)} ↗</a> · 분석 실행 ${esc(stamp(a.generated_at))}</p>` : ''}`, '근거 원장의 출처별 최신 수집 시각'));
      const claims = a.memo?.claims || [], ok = claims.filter(x => x.verified).length;
      parts.push(sec('메모 주장 검증', `<p class="dr-kv"><span>검증 통과 <b>${num(ok, { d: 0, src: `${aSrc(T)}#/memo/claims` })}</b>건</span><span>기각 <b>${num(claims.length - ok, { d: 0, src: `${aSrc(T)}#/memo/claims` })}</b>건</span><span>근거 원장 <b>${num((a.evidence || []).length, { d: 0, src: `${aSrc(T)}#/evidence` })}</b>건</span></p><p class="dr-fine">숫자·인용이 근거 원장의 기록과 일치하지 않는 문장은 게시되지 않습니다.</p>`));
    }
    const events = run?.events?.length ? run.events : a?.events || [];
    if (events.length || run) parts.push(sec('에이전트 결정 기록', `${run?.running ? '<p class="dr-live">실행 중…</p>' : ''}${run?.error ? `<p class="dr-note">실행 오류: ${esc(run.error)}</p>` : ''}${decisions(T, events, a)}`, run ? '실시간 실행' : '기록된 실행'));
    const pi = S.validation?.per_issuer?.[T];
    if (pi?.length) {
      const evs = new Map((S.validation.events || []).map(e => [e.id, e]));
      const rows = pi.map((r, j) => { const e = evs.get(r.event_id) || {}; const s = `${S.vsrc}#/per_issuer/${T}/${j}`; const cls = Number(r.car_pp) > 0 ? 'up' : Number(r.car_pp) < 0 ? 'down' : ''; return `<tr><th scope="row">${esc(unfix(e.name_ko) || r.event_id)}<small>${esc(dateOf(e.date))} · ${esc(unfix(r.filing) || '')}</small></th><td class="r">${num(r.exposure, { d: 2, src: `${s}/exposure` })}</td><td class="r ${cls}">${num(r.car_pp, { sign: true, unit: '%p', src: `${s}/car_pp` })}</td><td class="r">${num(r.quintile, { d: 0, src: `${s}/quintile` })}</td></tr>`; }).join('');
      parts.push(sec('과거 사건에서 이 종목', `<table class="dr-table"><thead><tr><th scope="col">사건 · 사건 전 공시</th><th scope="col" class="r">노출</th><th scope="col" class="r">초과수익 CAR</th><th scope="col" class="r">5분위</th></tr></thead><tbody>${rows}</tbody></table><p class="dr-fine">CAR = 사건일 −1~+5 거래일 시장모형 초과수익 · 5분위 = 사건별 노출 순위 (5 = 최고) · <button type="button" class="b-link" data-open-validation="1">전체 검증표 ›</button></p>`));
    } else if (S.validation) parts.push(sec('과거 사건에서 이 종목', `<p class="dr-dim">이 종목은 사건 검증 표본에 없습니다. <button type="button" class="b-link" data-open-validation="1">전체 검증표 ›</button></p>`));
    if (!a && !run) parts.unshift(noAnalysis(T, row, holdingRow(T).i));
    return parts.join('');
  }

  // ------------------------------------------------------------------- what if
  function scenarioOf(T, a) {
    if (!st.scen.has(T)) { const s = { ...DEFAULT_SCENARIO, ...(a?.scenario || {}) }; delete s.question; st.scen.set(T, s); }
    return st.scen.get(T);
  }
  function whatifTab(T, a, row, i) {
    if (!a?.stress?.inputs) return noAnalysis(T, row, i);
    const sc = scenarioOf(T, a);
    const sliders = CHANNEL_KEYS.map(k => `<label class="sc-row"><span>${CHANNELS[k].ko}</span><input type="range" min="0" max="100" step="5" value="${Math.round(sc[k] * 100)}" data-sc="${k}" aria-label="${CHANNELS[k].ko} 심각도"><output>${Math.round(sc[k] * 100)}%</output></label>`).join('');
    const horizon = `<label class="sc-row"><span>기간</span><select data-sc="horizon" aria-label="시나리오 기간">${[2026, 2027, 2028, 2029, 2030].map(y => `<option ${Number(sc.horizon) === y ? 'selected' : ''}>${y}</option>`).join('')}</select><output>${sc.horizon}년까지</output></label>`;
    return `${sec('시나리오', `<div class="sc-grid">${sliders}${horizon}</div><p class="dr-fine">심각도 = 해당 경로 충격의 발생 가능성과 깊이 · <button type="button" class="b-link" data-sc-reset="1">기본 시나리오로</button></p>`, '경로별 심각도 0–100%')}
      <section class="dr-sec" id="wi-out" aria-live="polite"></section>
      ${sec('역스트레스', '<ul class="dr-rev" id="wi-rev"><li class="dr-dim">계산 중…</li></ul><p class="dr-fine">FCF −20% 충격이 되려면 필요한 최소 심각도 · 해당 경로만 충격, 나머지 경로 0 · 같은 시드의 브라우저 재현을 이분 탐색</p>', '현금흐름 하위 5% ≤ −20%')}`;
  }
  function sameScenario(a, sc) { const s = a.scenario || {}; return [...CHANNEL_KEYS, 'horizon'].every(k => Math.abs(Number(s[k]) - Number(sc[k])) < 1e-9); }
  function stressFor(a, sc) { return sameScenario(a, sc) ? a.stress : runStressReplica({ ...a.stress.inputs, __code: a.stress.code || '' }, sc); }
  function computeWhatIf(T, a, withReverse) {
    const out = aside.querySelector('#wi-out'); if (!out) return;
    const sc = scenarioOf(T, a), stored = sameScenario(a, sc), s = stressFor(a, sc);
    const qs = new URLSearchParams(Object.fromEntries([...CHANNEL_KEYS, 'horizon'].map(k => [k, sc[k]]))).toString();
    const src = k => stored ? `${aSrc(T)}#/stress/${k}` : `replica:${T}?${qs}#/${k}`;
    const ms = stored ? a.metrics : recomputeScenarioMetrics(a.metrics, s), pol = policy(ms), srsI = ms.findIndex(m => m.code === 'SRS');
    const srsSrc = stored ? `${aSrc(T)}#/metrics/${srsI}/value` : `replica:${T}?${qs}#/metrics/SRS`;
    const h = s.histogram || {};
    out.innerHTML = `<h3>결과 <small>${stored ? '기록된 샌드박스 실행' : '브라우저 재현 · 같은 시드'} · ${fmt(s.paths, 0)}개 경로 · 시드 ${esc(s.seed)}</small></h3>
      <div class="wi-nums">
        <div><h4>현금흐름 하위 5%</h4><p class="wi-big">${num(s.fcf_shock_pct?.p05, { unit: '%', src: src('fcf_shock_pct/p05') })}</p></div>
        <div><h4>현금흐름 중앙값</h4><p class="wi-big">${num(s.fcf_shock_pct?.p50, { unit: '%', src: src('fcf_shock_pct/p50') })}</p></div>
        <div><h4>현금흐름 −20% 이하 확률</h4><p class="wi-big">${num((s.prob_fcf_drawdown_gt_20 ?? NaN) * 100, { unit: '%', src: src('prob_fcf_drawdown_gt_20') })}</p></div>
      </div>
      <figure class="wi-hist">${histogram(h.counts, h.edges, [{ v: Number(s.revenue_shock_pct?.p05), label: `P05 ${fmt(s.revenue_shock_pct?.p05)}%` }, { v: Number(s.revenue_shock_pct?.p50), label: `P50 ${fmt(s.revenue_shock_pct?.p50)}%` }])}<figcaption>매출 충격 분포 · 매출 P05 ${num(s.revenue_shock_pct?.p05, { unit: '%', src: src('revenue_shock_pct/p05') })} · P50 ${num(s.revenue_shock_pct?.p50, { unit: '%', src: src('revenue_shock_pct/p50') })}</figcaption></figure>
      ${pol ? `<p class="wi-verdict">이 시나리오의 판단 ${chip(pol.code)} <span class="dr-dim">시나리오 충격 SRS ${num(ms[srsI]?.value, { src: srsSrc })} · 규칙 ${pol.rule + 1}</span></p>` : ''}`;
    if (withReverse) scheduleReverse(T, a, 0); else scheduleReverse(T, a, 280);
  }
  function scheduleReverse(T, a, delay) {
    clearTimeout(st.revTimer); const token = ++st.revToken;
    st.revTimer = setTimeout(() => reverse(T, a, token), delay);
  }
  async function reverse(T, a, token) {
    const el = () => aside.querySelector('#wi-rev');
    // Single-channel reverse stress: the other three channels at 0, horizon from the sliders.
    const cur = scenarioOf(T, a), sc = { taiwan: 0, china_export: 0, hormuz: 0, malacca: 0, horizon: cur.horizon }, key = JSON.stringify(sc);
    const inputs = { ...a.stress.inputs, __code: '' };
    const p05 = async (k, v) => { await new Promise(r => setTimeout(r, 0)); if (token !== st.revToken) throw new Error('stale'); return runStressReplica(inputs, { ...sc, [k]: v }).fcf_shock_pct.p05; };
    const lines = [];
    try {
      for (const k of ['taiwan', 'hormuz']) {
        const cacheKey = `${T}|${k}|${key}`;
        let r = st.rev.get(cacheKey);
        if (!r) {
          const top = await p05(k, 1);
          if (top > -20) r = { reached: false, at1: top };
          else if (await p05(k, 0) <= -20) r = { reached: true, sev: 0 };
          else { let lo = 0, hi = 1; for (let n = 0; n < 9; n++) { const mid = (lo + hi) / 2; if (await p05(k, mid) <= -20) hi = mid; else lo = mid; } r = { reached: true, sev: Math.ceil(hi * 100) / 100 }; }
          st.rev.set(cacheKey, r);
        }
        const now = `현재 슬라이더 ${fmt(cur[k], 2)}`;
        lines.push(`<li><span>${CHANNELS[k].ko} 단독</span>${r.reached ? `<b>심각도 ≥ <span class="num" data-value="${raw(r.sev, 2)}" data-source="replica:${esc(T)}?reverse=${k}&horizon=${sc.horizon}">${fmt(r.sev, 2)}</span></b><span class="dr-dim">${now}</span>` : `<b>도달하지 않음</b><span class="dr-dim">심각도 1.00에서 하위 5% ${fmt(r.at1)}%</span>`}</li>`);
      }
    } catch { return; }
    if (token === st.revToken && el()) el().innerHTML = lines.join('');
  }

  // ------------------------------------------------------------------- act
  function actTab(T, a, row) {
    const run = st.live.get(T);
    const parts = [];
    const pol = a ? policy(a.metrics) : null;
    const code = row?.verdict || a?.memo?.recommendation || pol?.code;
    if (code) {
      const metricVal = c => { const m = (a?.metrics || []).find(x => x.code === c); const e = a && findEv(a, x => (x.label || '').startsWith(`${c} —`)); return m ? `${METRICS[c].ko} ${c} ${num(m.value, { src: `${aSrc(T)}#/metrics/${a.metrics.indexOf(m)}/value`, ev: e?.id, ledger: ledgerA(T) })}` : c; };
      const rules = RULES.map(([k, txt], j) => `<li${pol?.rule === j ? ' aria-current="true" class="on"' : ''}>${chip(k, { small: true })}<span>${txt}</span></li>`).join('');
      const why = pol ? `<p class="dr-why">이 종목: ${pol.why.map(([c, , cond]) => `${metricVal(c)} ${cond}`).join(', ')} → 규칙 ${pol.rule + 1}</p>` : '';
      const differs = pol && row?.verdict && pol.code !== row.verdict ? `<p class="dr-fine">포트폴리오 판단(${esc(VERDICT[row.verdict]?.ko || row.verdict)})은 공동 시나리오 기준이고, 규칙 계산은 종목 단독 분석 기준입니다.</p>` : '';
      parts.push(sec('판단', `<p class="dr-verdict">${chip(code)}</p><ol class="dr-rules">${rules}</ol>${why}${differs}<p class="dr-fine">규칙 출처: <a href="https://github.com/cjw0076/geo-alpha-sentinel/blob/main/sentinel/agent/runner.py" target="_blank" rel="noopener">sentinel/agent/runner.py · _recommendation ↗</a></p>`, '위험위원회 검토 행동 · 종목 추천 아님'));
    }
    if (run?.running) parts.push(sec('검증 메모', `<p class="dr-live">에이전트가 메모를 작성하고 있습니다 · ${run.events.length}단계</p>`));
    if (a?.memo) {
      const m = a.memo;
      parts.push(sec('검증 메모', `${run?.question ? `<p class="dr-q">질문: ${esc(run.question)}</p>` : ''}<p class="dr-memo">${evidenceText(m.summary, ledgerA(T))}</p><details class="dr-more"><summary>메모 전문 · ${(m.sections || []).length}개 절</summary>${(m.sections || []).map(x => `<h4>${esc(x.heading)}</h4><p>${evidenceText(x.body, ledgerA(T))}</p>`).join('')}</details>`, '검증기 통과 문장만'));
      if ((m.hedges || []).length) parts.push(sec('헤지·대응 아이디어', `<ul class="dr-hedges">${m.hedges.map(h => `<li><b>${esc(h.theme)}</b><p>${esc(h.rationale)}</p></li>`).join('')}</ul>`, '예시 · 투자 권유 아님'));
    }
    if (!a && !run) parts.push(noAnalysis(T, row, holdingRow(T).i));
    parts.push(`<div class="dr-actions">${a?.memo ? '<button type="button" class="btn" data-dr-print="1">IC 메모 인쇄</button>' : ''}<a class="b-link" href="${esc(S.modeURL('pro', { t: T, f: 'MEMO' }))}">전문가 모드에서 보기 →</a></div>`);
    return parts.join('');
  }
  async function printMemo(T) {
    const a = await analysis(T); if (!a?.memo) return;
    const ledger = evMap(a);
    const text = [a.memo.summary, ...(a.memo.sections || []).map(x => x.body), ...(a.memo.claims || []).map(x => x.text), ...(a.memo.hedges || []).map(x => x.rationale)].join(' ');
    const ids = [...new Set([...[...text.matchAll(/\[(E\d+)\]/g)].map(x => x[1]), ...(a.memo.claims || []).flatMap(c => c.evidence_ids || [])])];
    const appendix = `<section class="print-evidence"><h3>EVIDENCE APPENDIX · 근거 목록</h3><ol>${ids.map(id => ledger.get(id)).filter(Boolean).map(e => `<li><b>${esc(e.id)}</b> ${esc(e.label)} · ${esc(e.provenance?.title || '')}<br><a href="${esc(e.provenance?.url || '#')}">${esc(e.provenance?.url || '')}</a></li>`).join('')}</ol></section>`;
    const host = document.getElementById('b-print');
    host.innerHTML = `<div class="panel" data-panel="MEMO"><div class="panel-head"><b>MEMO</b><span>Verified memo</span><span class="kr">검증 메모 · ${esc(T)}</span></div><div class="panel-body">${renderMEMO({ result: a, evidence: ledger })}${appendix}</div></div>`;
    window.print();
  }

  // ------------------------------------------------------------------- chokepoint / channel
  function resolveChoke(id) {
    const chs = S.portfolio.channels || [];
    const ch = chs.find(c => c.chokepoint_id === id || c.id === id) || null;
    const cpId = ch?.chokepoint_id || CHANNELS[ch?.id]?.cp || (String(id).startsWith('chokepoint') ? id : null);
    const items = S.chokepoints?.items || [], xi = items.findIndex(x => x.id === cpId);
    return { ch, ci: chs.indexOf(ch), item: items[xi] || null, xi };
  }
  async function renderChoke() {
    const c = st.cur, { ch, ci, item, xi } = resolveChoke(c.id);
    const cs = `${S.chokeSrc}#/items/${xi}`, ps = `${S.src}#/channels/${ci}`;
    const title = item ? esc(koChokepoint(item.name)) : esc(unfix(ch?.label_ko) || CHANNELS[ch?.id]?.ko || c.id);
    const sub = item ? `<span lang="en">${esc(item.name)}</span>` : esc(unfix(ch?.label_en) || '');
    const meta = item ? `<span>365일 대비 ${num(item.delta_pct, { sign: true, unit: '%', src: `${cs}/delta_pct`, cls: Number(item.delta_pct) <= -25 ? 'alert' : '' })}</span><span>7일 평균 ${num(item.mean_7d, { src: `${cs}/mean_7d` })}척/일</span>${ch ? `<span>초과 노출 ${num(ch.active_pp, { sign: true, unit: '%p', src: `${ps}/active_pp` })}</span>` : ''}` : ch ? `<span>초과 노출 ${num(ch.active_pp, { sign: true, unit: '%p', src: `${ps}/active_pp` })}</span>` : '';
    const eyebrow = item ? '해상 병목 · IMF PortWatch' : '노출 경로';
    let body = '';
    if (c.tab === 'why') {
      const series = (item?.series || []).map(p => ({ t: dateOf(p.date), v: p.n_total }));
      const chart = item ? `<figure class="dr-chart">${lineChart(series, { baseline: Number(item.baseline_365d), label: `${item.name} 일일 통항 ${series.length}일` })}<figcaption>일일 통항 척수 · 최근 ${series.length}일 · 점선 = 365일 평균</figcaption></figure>
        <dl class="dr-grid"><div><dt>${esc(dateOf(item.latest?.date))} 통항</dt><dd>${num(item.latest?.n_total, { d: 0, unit: '척', src: `${cs}/latest/n_total` })}</dd></div><div><dt>7일 평균</dt><dd>${num(item.mean_7d, { unit: '척/일', src: `${cs}/mean_7d` })}</dd></div><div><dt>365일 평균</dt><dd>${num(item.baseline_365d, { unit: '척/일', src: `${cs}/baseline_365d` })}</dd></div><div><dt>변화</dt><dd>${num(item.delta_pct, { sign: true, unit: '%', src: `${cs}/delta_pct` })}</dd></div></dl><p class="dr-fine">출처 IMF PortWatch (AIS) · ${esc(item.provenance?.locator || item.id)} · 수집 ${esc(stamp(item.provenance?.retrieved_at))} · <a href="${esc(/^https?:/.test(item.provenance?.url || '') ? item.provenance.url : 'https://portwatch.imf.org/')}" target="_blank" rel="noopener">PortWatch ↗</a></p>` : '';
      const port = ch ? sec('포트폴리오 노출', `<dl class="dr-grid"><div><dt>${esc(unfix(S.portfolio.book?.name_ko) || '보유')}</dt><dd>${num(ch.book_pct, { unit: '%', src: `${ps}/book_pct` })}</dd></div><div><dt>벤치마크</dt><dd>${num(ch.bench_pct, { unit: '%', src: `${ps}/bench_pct` })}</dd></div><div><dt>초과 노출</dt><dd>${num(ch.active_pp, { sign: true, unit: '%p', src: `${ps}/active_pp` })}</dd></div><div><dt>유효 종목 수</dt><dd>${num(ch.effective_n, { src: `${ps}/effective_n` })}</dd></div></dl><p class="dr-fine">노출 = 보유 비중 × 종목별 경로 노출도(0–1)의 합 · 유효 종목 수가 작을수록 소수 종목에 집중</p>`, esc(unfix(ch.label_ko) || CHANNELS[ch.id]?.ko || '')) : '';
      let mineralSec = '';
      if (!item && ch?.id === 'china_export') {
        const m = await loadMinerals(); if (st.cur !== c) return;
        const rows = (m?.items || []).filter(x => x.china_share_pct != null).sort((x, y) => y.china_share_pct - x.china_share_pct).slice(0, 8);
        mineralSec = rows.length ? sec('중국 생산 비중이 큰 광물', `<table class="dr-table"><thead><tr><th scope="col">광물</th><th scope="col" class="r">중국 비중</th><th scope="col" class="r">집중도 HHI</th></tr></thead><tbody>${rows.map(x => { const j = m.items.indexOf(x); return `<tr><th scope="row">${esc(x.name)}<small>${esc(x.measure || '')} · ${esc(x.year || '')}</small></th><td class="r">${num(x.china_share_pct, { unit: '%', src: `${S.base.replace(/^\.\//, '')}/minerals.json#/items/${j}/china_share_pct` })}</td><td class="r">${num(x.hhi, { d: 0, src: `${S.base.replace(/^\.\//, '')}/minerals.json#/items/${j}/hhi` })}</td></tr>`; }).join('')}</tbody></table>`, 'USGS Mineral Commodity Summaries') : '';
      }
      body = `${item ? sec('통항 추이', chart, 'IMF PortWatch · AIS') : ''}${port}${mineralSec}`;
    } else if (c.tab === 'sure') {
      const p = item?.provenance || {};
      body = item ? sec('출처와 기준일', `<dl class="dr-prov"><dt>출처</dt><dd>IMF PortWatch · 선박자동식별(AIS) 신호로 집계한 일일 통항</dd><dt>기준일</dt><dd>${esc(dateOf(item.latest?.date))}${dateOf(S.chokepoints?.as_of) !== dateOf(item.latest?.date) ? ` (전체 ${esc(dateOf(S.chokepoints?.as_of))})` : ''}</dd><dt>수집 시각</dt><dd>${esc(stamp(p.retrieved_at))}</dd><dt>전송</dt><dd>${{ live: '실시간 조회', mock: '캡처된 PortWatch 응답 (mock)' }[S.chokepoints?.source] || '정적 스냅숏'} · ${esc(S.chokeSrc || '')}</dd><dt>라이선스</dt><dd>${esc(p.license || '—')}</dd><dt>위치</dt><dd>${esc(p.locator || item.id)}</dd></dl><p><a class="btn" href="${esc(p.url || 'https://portwatch.imf.org/')}" target="_blank" rel="noopener">PortWatch에서 보기 ↗</a></p><p class="dr-fine">척수는 선박 수이며 화물량이 아닙니다. 변화율 = 최근 7일 평균 ÷ 직전 365일 평균 − 1.</p>`)
        : sec('출처', `<p>이 경로는 해상 통항 지표가 아니라 공시 문장(수출통제·광물)과 USGS 생산 집중도로 계산합니다.</p><p><a href="${DOCS}METHODOLOGY.md" target="_blank" rel="noopener">방법론 ↗</a></p>`);
    } else if (c.tab === 'whatif') {
      body = ch ? sec('이 경로가 막히면 영향이 큰 보유 종목', `<table class="dr-table dr-click"><thead><tr><th scope="col">종목</th><th scope="col" class="r">비중</th><th scope="col" class="r">노출도</th><th scope="col" class="r">기여 %p</th></tr></thead><tbody>${(ch.top || []).map((x, j) => `<tr data-open-ticker="${esc(x.ticker)}" tabindex="0"><th scope="row"><b class="tk">${esc(x.ticker)}</b><small>${esc(prettyName(x.name))}</small></th><td class="r">${num(x.weight_pct, { d: 2, unit: '%', src: `${ps}/top/${j}/weight_pct` })}</td><td class="r">${num(x.exposure, { d: 2, src: `${ps}/top/${j}/exposure`, ev: x.evidence_id, ledger: 'portfolio' })}</td><td class="r">${num(x.contribution_pp, { d: 2, src: `${ps}/top/${j}/contribution_pp` })}</td></tr>`).join('')}</tbody></table><p class="dr-fine">기여 = 비중 × 노출도 · 행을 누르면 종목 상세</p>`, '경로 노출 기여 상위') : unlinked();
    } else {
      if (!ch) body = unlinked();
      else {
        const hs = new Map((S.portfolio.holdings || []).map((h, j) => [h.ticker, [h, j]]));
        const rows = (ch.top || []).map(x => { const [h, j] = hs.get(x.ticker) || []; return `<tr data-open-ticker="${esc(x.ticker)}" tabindex="0"><th scope="row"><b class="tk">${esc(x.ticker)}</b><small>${esc(prettyName(x.name))}</small></th><td>${chip(h?.verdict, { small: true })}</td><td class="r">${h ? num(h.p05_pct, { unit: '%', src: `${S.src}#/holdings/${j}/p05_pct` }) : '—'}</td></tr>`; }).join('');
        const counts = {}; for (const x of ch.top || []) { const v = hs.get(x.ticker)?.[0]?.verdict; if (v) counts[v] = (counts[v] || 0) + 1; }
        const tally = Object.keys(VERDICT).filter(k => counts[k]).map(k => `${VERDICT[k].ko} ${counts[k]}`).join(' · ');
        body = sec('이 종목들의 판단', `${tally ? `<p class="dr-kv">${esc(tally)}</p>` : ''}<table class="dr-table dr-click"><thead><tr><th scope="col">종목</th><th scope="col">판단</th><th scope="col" class="r">하위 5%</th></tr></thead><tbody>${rows}</tbody></table><p class="dr-fine">판단은 정책 규칙(위험위원회 검토 행동)의 결과이며 종목 추천이 아닙니다.</p>`);
      }
    }
    frame(head({ eyebrow, title, sub, meta }), body, c.tab);
  }
  const unlinked = () => '<p class="dr-dim">이 병목과 연결된 포트폴리오 경로가 없습니다. 포트폴리오 경로: 대만 해협 · 중국 수출통제 · 호르무즈 · 말라카.</p>';

  // ------------------------------------------------------------------- evidence, validation
  async function renderEvidence() {
    const c = st.cur, back = st.stack.length > 0;
    const list = await ledgerRecords(c.ledger); if (st.cur !== c) return;
    const e = list.find(x => x.id === c.id);
    const ledgerName = c.ledger === 'portfolio' ? `${unfix(S.portfolio.book?.name_ko) || '포트폴리오'} 근거 원장` : `${c.ledger.slice(2)} 분석 근거 원장`;
    if (!e) { frame(head({ eyebrow: `근거 ${esc(c.id)}`, title: '기록을 찾을 수 없습니다', back }), `<p class="dr-dim">${esc(ledgerName)}에 ${esc(c.id)}가 없습니다.</p>`); return; }
    const p = e.provenance || {}, i = list.indexOf(e);
    const file = c.ledger === 'portfolio' ? S.src : aSrc(c.ledger.slice(2));
    const value = e.value != null ? `<p class="dr-ev-val">${num(e.value, { d: Math.abs(e.value) >= 1000 ? 0 : 2, src: `${file}#/evidence/${i}/value`, ev: e.id, ledger: c.ledger })} <small>${esc(unfix(e.unit) || '')}</small></p>` : '';
    const quote = e.text ? `<blockquote><p>“${esc(e.text)}”</p></blockquote>` : '';
    const safe = /^https?:\/\//.test(p.url || '');
    frame(head({ eyebrow: `근거 ${esc(e.id)} · ${esc(ledgerName)}`, title: esc(e.label), back }), `${value}${quote}<dl class="dr-prov"><dt>출처</dt><dd>${esc(sourceName(p.source))}</dd><dt>제목</dt><dd>${esc(p.title || '—')}</dd><dt>위치</dt><dd>${esc(p.locator || '—')}</dd><dt>수집 시각</dt><dd>${esc(p.retrieved_at || '—')}</dd><dt>라이선스</dt><dd>${esc(p.license || '—')}</dd></dl>${safe ? `<p><a class="btn" href="${esc(p.url)}" target="_blank" rel="noopener">원문 열기 ↗</a></p>` : `<p class="dr-dim">${esc(p.url || '')}</p>`}`);
  }
  function renderValidation() {
    const v = S.validation, back = st.stack.length > 0;
    if (!v) { frame(head({ eyebrow: '과거 사건 검증', title: '검증 결과 파일이 아직 없습니다', back }), ''); return; }
    const sm = v.summary || {}, vs = S.vsrc;
    const rows = (v.events || []).map((e, j) => {
      const s = `${vs}#/events/${j}`, link = [e.source_url, e.crosscheck_url].find(u => /^https?:/.test(u || ''));
      // q5q1_pp is null when quintiles are degenerate; the engine then reports the spread between the extreme non-empty quintiles.
      const alt = e.q5q1_pp == null && e.q_extreme_spread_pp != null, qv = alt ? e.q_extreme_spread_pp : e.q5q1_pp, q = Number(qv);
      const qCell = `${num(qv, { sign: true, src: `${s}/${alt ? 'q_extreme_spread_pp' : 'q5q1_pp'}` })}${alt && e.q_extreme ? `<small>Q${esc(e.q_extreme[1])}−Q${esc(e.q_extreme[0])}</small>` : ''}`;
      return `<tr><th scope="row">${esc(unfix(e.name_ko))}<small>${esc(dateOf(e.date))} · ${esc(CHANNELS[e.channel]?.short || e.channel)}${link ? ` · <a href="${esc(link)}" target="_blank" rel="noopener">출처 ↗</a>` : ''}</small></th><td class="r">${num(e.n, { d: 0, src: `${s}/n` })}</td><td class="r">${num(e.ic, { d: 2, src: `${s}/ic` })}<small>${fmt(e.ic_ci?.[0], 2)} ~ ${fmt(e.ic_ci?.[1], 2)}</small></td><td class="r ${q > 0 ? 'up' : q < 0 ? 'down' : ''}">${qCell}</td><td class="c">${e.sign_ok ? '<span class="ok">일치</span>' : '<span class="no">불일치</span>'}</td></tr>`;
    }).join('');
    const w = v.windows || {};
    const body = `<dl class="dr-grid"><div><dt>방향 일치</dt><dd>${num(sm.sign_ok_n, { d: 0, src: `${vs}#/summary/sign_ok_n` })}<span class="dr-of">/${fmt(sm.events_n, 0)}</span></dd></div><div><dt>평균 IC</dt><dd>${num(sm.mean_ic, { d: 2, src: `${vs}#/summary/mean_ic` })}</dd></div><div><dt>10% 유의</dt><dd>${num(sm.significant_10pct_n, { d: 0, src: `${vs}#/summary/significant_10pct_n` })}건</dd></div><div><dt>기준선 IC</dt><dd class="dr-small">업종 ${num(sm.baseline_sector_mean_ic, { d: 2, src: `${vs}#/summary/baseline_sector_mean_ic` })} · 언급 ${num(sm.baseline_mention_mean_ic, { d: 2, src: `${vs}#/summary/baseline_mention_mean_ic` })}</dd></div></dl>
      ${sec('사건별 결과', `<div class="b-scroll"><table class="dr-table"><thead><tr><th scope="col">사건</th><th scope="col" class="r">n</th><th scope="col" class="r">IC · 95% 구간</th><th scope="col" class="r">Q5−Q1 %p</th><th scope="col" class="c">방향</th></tr></thead><tbody>${rows}</tbody></table></div>`, '노출이 클수록 수익률이 낮으면 음의 IC = 예상 방향')}
      <p class="dr-fine">사건과 기간은 계산 전에 선언했습니다 (${esc(stamp(v.declared_at))}). 주 창 [${(w.primary || []).join(', ')}] · 보조 창 [${(w.secondary || []).join(', ')}] 거래일 · 추정 [${(w.estimation || []).join(', ')}] · 벤치마크 ${esc(unfix(v.benchmark))} · 노출은 사건 전에 제출된 공시만 사용. Q5−Q1 = 노출 최상위 5분위와 최하위 5분위의 평균 초과수익 차.</p>
      <p><a class="b-link" href="${DOCS}VALIDATION.md" target="_blank" rel="noopener">검증 보고서 VALIDATION.md ↗</a></p>`;
    frame(head({ eyebrow: `과거 사건 검증 · ${esc(unfix(v.methodology_version))}`, title: '사건 전 공시로 잰 노출이 사건 수익률의 순위를 맞혔는가', back }), body);
  }

  // ------------------------------------------------------------------- dispatch
  async function render(focusTab = false) {
    const c = st.cur; if (!c) return;
    if (c.kind === 'holding') await renderHolding();
    else if (c.kind === 'chokepoint') await renderChoke();
    else if (c.kind === 'evidence') await renderEvidence();
    else renderValidation();
    if (st.cur !== c) return;
    syncURL();
    if (focusTab === 'tab') aside.querySelector('[role="tab"][aria-selected="true"]')?.focus({ preventScroll: true });
    else if (focusTab && !aside.contains(document.activeElement)) aside.focus({ preventScroll: true });
  }
  function open(next, { push = false } = {}) {
    if (push && isOpen() && st.cur && (st.cur.kind === 'holding' || st.cur.kind === 'chokepoint' || st.cur.kind === 'validation')) st.stack.push(st.cur);
    else if (!push) st.stack = [];
    st.cur = next; show();
    aside.scrollTop = 0;
    render(true);
  }
  function runLive(T, question) {
    if (!S.api || S.mode !== 'LIVE') return;
    const prev = st.live.get(T); if (prev?.running) return;
    const run = { events: [], running: true, question, error: null };
    st.live.set(T, run);
    const sc = scenarioOf(T, null);
    run.handle = S.api.analyze(T, sc, question || '', e => { if (!e) return; run.events.push(e); if (st.cur?.kind === 'holding' && st.cur.id === T) render(); }, result => {
      run.running = false; st.analyses.set(T, Promise.resolve(result));
      if (st.cur?.kind === 'holding' && st.cur.id === T) { st.cur = { ...st.cur, tab: 'act' }; render(); }
    }, message => { run.running = false; run.error = message; if (st.cur?.kind === 'holding' && st.cur.id === T) render(); });
  }

  aside.addEventListener('click', e => {
    const el = e.target.closest('[data-dr-close],[data-dr-back],[data-dr-tab],[data-open-evidence],[data-open-ticker],[data-open-validation],[data-dr-choke],[data-dr-print],[data-dr-run],[data-sc-reset],.wire-node[data-evidence]');
    if (!el) return;
    if (el.matches('a')) return;
    e.preventDefault();
    if (el.dataset.drClose) close();
    else if (el.dataset.drBack) { st.cur = st.stack.pop() || null; if (st.cur) render(true); else close(); }
    else if (el.dataset.drTab) { st.cur = { ...st.cur, tab: el.dataset.drTab }; render('tab'); }
    else if (el.dataset.openEvidence) open({ kind: 'evidence', ledger: el.dataset.ledger || 'portfolio', id: el.dataset.openEvidence }, { push: true });
    else if (el.classList.contains('wire-node')) { if (el.dataset.evidence && st.cur?.kind === 'holding') open({ kind: 'evidence', ledger: ledgerA(st.cur.id), id: el.dataset.evidence }, { push: true }); }
    else if (el.dataset.openTicker) open({ kind: 'holding', id: el.dataset.openTicker, tab: 'why' });
    else if (el.dataset.openValidation) open({ kind: 'validation' }, { push: true });
    else if (el.dataset.drChoke) open({ kind: 'chokepoint', id: el.dataset.drChoke, tab: 'why' });
    else if (el.dataset.drPrint) printMemo(st.cur.id);
    else if (el.dataset.drRun) { runLive(el.dataset.drRun, ''); render(); }
    else if (el.dataset.scReset) { st.scen.delete(st.cur.id); render(); }
  });
  aside.addEventListener('toggle', e => {
    const d = e.target; if (!d.matches?.('details.dr-metric') || !d.open) return;
    const host = d.querySelector('.m-wire'); if (!host || host.dataset.filled) return;
    const T = st.cur?.id;
    analysis(T).then(a => {
      if (!a) return;
      host.innerHTML = renderRiskWiring({ metrics: a.metrics, evidence: evMap(a), selectedMetric: d.dataset.metric });
      host.dataset.filled = '1';
      requestAnimationFrame(() => drawEvidenceWires(host));
    });
  }, true);
  aside.addEventListener('input', e => {
    const k = e.target.dataset?.sc; if (!k || st.cur?.kind !== 'holding') return;
    const T = st.cur.id;
    analysis(T).then(a => {
      if (!a) return;
      const sc = scenarioOf(T, a);
      if (k === 'horizon') { sc.horizon = Number(e.target.value); e.target.nextElementSibling.textContent = `${sc.horizon}년까지`; }
      else { sc[k] = Number(e.target.value) / 100; e.target.nextElementSibling.textContent = `${e.target.value}%`; }
      clearTimeout(st.stressTimer); st.stressTimer = setTimeout(() => computeWhatIf(T, a, false), 60);
    });
  });
  aside.addEventListener('keydown', e => {
    if (e.target.matches?.('[role="tab"]') && ['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) {
      e.preventDefault(); const keys = TABS.map(x => x[0]), i = keys.indexOf(st.cur.tab);
      const n = e.key === 'Home' ? 0 : e.key === 'End' ? keys.length - 1 : (i + (e.key === 'ArrowRight' ? 1 : -1) + keys.length) % keys.length;
      st.cur = { ...st.cur, tab: keys[n] }; render('tab'); return;
    }
    if ((e.key === 'Enter' || e.key === ' ') && e.target.matches?.('tr[data-open-ticker]')) { e.preventDefault(); open({ kind: 'holding', id: e.target.dataset.openTicker, tab: 'why' }); return; }
    if (e.key === 'Tab') {
      const f = [...aside.querySelectorAll('a[href],button:not([disabled]),input,select,summary,[tabindex="0"]')].filter(x => x.offsetParent !== null);
      if (!f.length) return;
      if (e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f.at(-1).focus(); }
      else if (!e.shiftKey && document.activeElement === f.at(-1)) { e.preventDefault(); f[0].focus(); }
    }
  });
  scrim.addEventListener('click', close);
  window.addEventListener('resize', () => { for (const host of aside.querySelectorAll('.m-wire[data-filled]')) drawEvidenceWires(host); });

  return {
    isOpen,
    close,
    openHolding(T, { tab, question, fromURL } = {}) {
      const t = String(T || '').trim().toUpperCase(); if (!t) return;
      open({ kind: 'holding', id: t, tab: TABS.some(x => x[0] === tab) ? tab : 'why' });
      if (question) {
        const go = () => runLive(t, question);
        if (S.api) go(); else import('./api.js').then(async api => { await api.ready; S.api = api; S.mode = api.mode; go(); });
      }
      void fromURL;
    },
    openChokepoint(id, { tab } = {}) { open({ kind: 'chokepoint', id, tab: TABS.some(x => x[0] === tab) ? tab : 'why' }); },
    openEvidence(ledger, id) { open({ kind: 'evidence', ledger, id }, { push: true }); },
    openValidation() { open({ kind: 'validation' }, { push: true }); },
    refresh(what) { if (isOpen() && what === 'chokepoints' && (st.cur?.kind === 'chokepoint' || (st.cur?.kind === 'holding' && st.cur.tab === 'why'))) { const y = aside.querySelector('.dr-body')?.scrollTop || 0; render().then(() => { const b = aside.querySelector('.dr-body'); if (b) b.scrollTop = y; }); } },
  };
}
