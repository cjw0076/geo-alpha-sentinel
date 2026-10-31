const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const num=v=>Number(v).toLocaleString('en-US',{maximumFractionDigits:2});
const source=e=>({imf_portwatch:'IMF PORTWATCH',sec_edgar:'SEC EDGAR',usgs_mcs:'USGS MCS',sandbox:'SANDBOX',model:'MODEL',trade_csl:'TRADE.GOV CSL',fred:'FRED',nyfed_gscpi:'NY FED',un_comtrade:'UN COMTRADE'})[e?.provenance?.source]||String(e?.provenance?.source||'SOURCE').toUpperCase();
const value=e=>e?.value!=null?`${num(e.value)} ${esc(e.unit||'')}`:esc((e?.text||'').slice(0,70));
const evidenceCard=(id,e,comp)=>`<button class="wire-node wire-evidence" data-ev="${esc(id)}" data-comp="${comp}" data-evidence="${esc(id)}" data-wire-keys="${esc(id)}"><span class="wire-source">${esc(source(e))}</span><strong>${esc(e?.label||id)}</strong><span class="wire-value">${value(e)}</span><small>${esc(id)} · SOURCE ↗</small></button>`;
const metricCard=(m,id)=>`<button class="wire-node wire-metric" data-metric-node="1" data-evidence="${esc(id||'')}" data-wire-keys="${esc(id||'')}"><span>METRIC · 지표</span><strong>${esc(m.code)}</strong><b>${m.code==='FAR'?esc(m.rating||'—'):num(m.value)}</b><small>${esc(m.name||'')}</small></button>`;
export function renderRiskWiring(state){
  const m=(state.metrics||[]).find(x=>x.code===state.selectedMetric);if(!m)return '';
  const links=(m.components||[]).flatMap(c=>c.evidence_ids||[]);
  const hasLedger=links.some(id=>state.evidence?.has(id));
  const first=links.find(id=>state.evidence.has(id))||'';
  const records=hasLedger?(m.components||[]).flatMap((c,i)=>(c.evidence_ids||[]).filter(id=>state.evidence.has(id)).map(id=>evidenceCard(id,state.evidence.get(id),i))).join(''):'';
  const components=(m.components||[]).map((c,i)=>{
    const ids=(c.evidence_ids||[]).filter(id=>state.evidence.has(id)),ref=ids[0]||'';
    return `<button class="wire-node wire-component" data-comp-node="${i}" data-evidence="${esc(ref)}" data-wire-keys="${esc(ids.join(' '))}"><span>COMPONENT · 구성요소</span><strong>${esc(c.name)}</strong><div><b>${num(c.value)}</b><small> × ${num(c.weight)} = ${num(c.value*c.weight)}</small></div><small>${esc(c.note||'')}</small></button>`;
  }).join('');
  return `<div class="wire-diagram ${hasLedger?'':'wire-no-ledger'}" data-wire-kind="risk"><div class="wire-heading"><b>${esc(m.code)} · EVIDENCE CHAIN</b><span>${esc(m.formula||'')}</span></div>${hasLedger?'':'<div class="wire-await">Run AGNT to load the source ledger · 원문 근거를 보려면 에이전트를 실행하세요</div>'}<div class="wire-grid">${hasLedger?`<div class="wire-column wire-records"><h3>EVIDENCE <span>원문 근거</span></h3>${records}</div>`:''}<div class="wire-column wire-components"><h3>COMPONENT <span>계산 요소</span></h3>${components}</div><div class="wire-column wire-metrics"><h3>METRIC <span>합성 지표</span></h3>${metricCard(m,first)}</div></div><svg class="wire-lines" aria-hidden="true"></svg></div>`;
}
function metricForClaim(claim,state){
  const explicit=(claim.text||'').match(/\b(CPI|GPI|SRS|FAR)\b/);
  if(explicit)return (state.metrics||[]).find(m=>m.code===explicit[1]);
  return (state.metrics||[]).find(m=>(m.components||[]).some(c=>(c.evidence_ids||[]).some(id=>(claim.evidence_ids||[]).includes(id))));
}
export function renderMemoWiring(state){
  const claims=state.result?.memo?.claims||[];
  if(!claims.length)return '';
  return `<div class="memo-wire-list">${claims.map((c,i)=>{
    const ids=(c.evidence_ids||[]).filter(id=>state.evidence.has(id)),m=metricForClaim(c,state),first=ids[0]||'';
    const metric=m?metricCard(m,first):`<button class="wire-node wire-metric" data-metric-node="1" data-evidence="${esc(first)}" data-wire-keys="${esc(ids.join(' '))}"><span>VERIFIED CLAIM · 검증 주장</span><strong>${c.verified?'VERIFIED':'REJECTED'}</strong></button>`;
    return `<div class="wire-diagram memo-wire" data-wire-kind="memo"><div class="wire-grid"><div class="wire-column wire-records"><h3>EVIDENCE <span>원문 근거</span></h3>${ids.map(id=>evidenceCard(id,state.evidence.get(id),0)).join('')||'<span class="wire-await">NO LEDGER RECORD</span>'}</div><div class="wire-column wire-components"><h3>CLAIM <span>검증 주장</span></h3><button class="wire-node wire-component ${c.verified?'':'wire-rejected'}" data-comp-node="0" data-evidence="${esc(first)}" data-wire-keys="${esc(ids.join(' '))}"><span>${c.verified?'VERIFIED':'REJECTED'} · ${i+1}</span><strong>${esc(c.text)}</strong>${c.reason?`<small>${esc(c.reason)}</small>`:''}</button></div><div class="wire-column wire-metrics"><h3>METRIC <span>연결 지표</span></h3>${metric}</div></div><svg class="wire-lines" aria-hidden="true"></svg></div>`;
  }).join('')}</div>`;
}
export function drawEvidenceWires(panel){
  if(!panel)return;
  for(const root of panel.querySelectorAll('.wire-diagram')){
    const svg=root.querySelector('.wire-lines'),box=root.getBoundingClientRect(),grid=root.querySelector('.wire-grid');
    if(!svg||!grid)continue;
    if(root.dataset.wireKind==='risk'){
      const column=root.querySelector('.wire-components'),columnRect=column.getBoundingClientRect();
      const allRecords=[...root.querySelectorAll('[data-ev]')];
      for(const comp of column.querySelectorAll('[data-comp-node]')){
        const records=allRecords.filter(n=>n.dataset.comp===comp.dataset.compNode);
        if(!records.length)continue;
        const first=records[0].getBoundingClientRect(),last=records.at(-1).getBoundingClientRect();
        comp.style.top=`${Math.max(28,(first.top+last.bottom)/2-columnRect.top-comp.offsetHeight/2)}px`;
      }
      const metric=root.querySelector('[data-metric-node]'),metricColumn=root.querySelector('.wire-metrics');
      if(metric&&allRecords.length){const first=allRecords[0].getBoundingClientRect(),last=allRecords.at(-1).getBoundingClientRect();metric.style.top=`${Math.max(28,(first.top+last.bottom)/2-metricColumn.getBoundingClientRect().top-metric.offsetHeight/2)}px`;}
    }
    const height=grid.scrollHeight,width=grid.scrollWidth,rect=grid.getBoundingClientRect();
    svg.setAttribute('viewBox',`0 0 ${width} ${height}`);svg.style.width=`${width}px`;svg.style.height=`${height}px`;svg.style.left=`${rect.left-box.left}px`;svg.style.top=`${rect.top-box.top}px`;
    const center=(el,side)=>{const r=el.getBoundingClientRect();return [side==='right'?r.right-rect.left:r.left-rect.left,r.top-rect.top+r.height/2];};
    const path=(from,to,keys)=>{const [x1,y1]=center(from,'right'),[x2,y2]=center(to,'left'),dx=Math.max(14,(x2-x1)*.46),d=`M${x1},${y1} C${x1+dx},${y1} ${x2-dx},${y2} ${x2},${y2}`;return `<path class="wire-path" data-wire-keys="${esc(keys)}" d="${d}"/>`;};
    let lines='';const metric=root.querySelector('[data-metric-node]');
    for(const comp of root.querySelectorAll('[data-comp-node]')){
      const ids=(comp.dataset.wireKeys||'').split(/\s+/).filter(Boolean);
      for(const id of ids){const ev=[...root.querySelectorAll('[data-ev]')].find(n=>n.dataset.ev===id&&n.dataset.comp===comp.dataset.compNode);if(ev)lines+=path(ev,comp,id);}
      if(metric)lines+=path(comp,metric,ids.join(' '));
    }
    svg.innerHTML=lines;
    for(const node of root.querySelectorAll('.wire-node')){
      node.onpointerenter=()=>{const keys=(node.dataset.wireKeys||'').split(/\s+/).filter(Boolean);root.classList.add('wire-hover');for(const n of root.querySelectorAll('.wire-node,.wire-path'))n.classList.toggle('is-linked',!keys.length||keys.some(k=>(n.dataset.wireKeys||'').split(/\s+/).includes(k)));};
      node.onpointerleave=()=>{root.classList.remove('wire-hover');for(const n of root.querySelectorAll('.is-linked'))n.classList.remove('is-linked');};
    }
  }
}
