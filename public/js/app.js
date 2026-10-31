import {mock,mode,modeInfo,ready,getJSON,getIndex,search,scenarioQuery,analyze,onLinkStatus,onModeChange,onLiveChokepoints,memoURL} from './api.js';
import {FUNCTIONS,esc,renderDES,renderRISK,render4W,renderSTRS,renderAGNT,renderMEMO,renderCHKP,renderMINR,renderSANC,renderMACR,renderHELP} from './panels.js';
import {renderSCRN} from './screener.js';
import {renderRiskWiring,renderMemoWiring,drawEvidenceWires as drawWires} from './evidence.js';

const $=id=>document.getElementById(id), params=new URLSearchParams(location.search);
const codes=FUNCTIONS.map(x=>x[0]);
const limit=(v,d)=>v==null?d:Math.max(0,Math.min(1,Number(v)||0));
const state={ticker:(params.get('t')||'').toUpperCase(),fn:codes.includes((params.get('f')||'').toUpperCase())?params.get('f').toUpperCase():null,mode:'CONNECTING',index:null,missingTicker:null,company:null,exposure:null,chokepoints:null,minerals:null,macro:null,sanctions:null,stress:null,metrics:[],events:[],result:null,evidence:new Map(),selectedMetric:null,chkpSort:'delta_pct',scrnSort:'ticker',scrnDesc:false,showCode:false,question:'',sanctionsQuery:'',running:false,scenario:{taiwan:limit(params.get('taiwan'),.5),china_export:limit(params.get('china_export'),.5),hormuz:limit(params.get('hormuz'),.2),malacca:limit(params.get('malacca'),.2),horizon:Math.max(2026,Math.min(2030,Number(params.get('h'))||2027))}};
const layouts=['DES','RISK','WGLB','4W','STRS','AGNT','MEMO','CHKP','MINR','SCRN','SANC','MACR','HELP'];
const renders={DES:renderDES,RISK:renderRISK,'4W':render4W,STRS:renderSTRS,AGNT:renderAGNT,MEMO:renderMEMO,CHKP:renderCHKP,MINR:renderMINR,SCRN:renderSCRN,SANC:renderSANC,MACR:renderMACR,HELP:renderHELP,WGLB:()=>'<div id="globe-host" class="globe-host"></div>'};
const sources={DES:'SEC EDGAR',RISK:'MODEL · COMPUTED','4W':'SEC ITEM 1A',STRS:'SANDBOX · SERVER',AGNT:'MULTI-AGENT · TRACE',MEMO:'EVIDENCE VERIFIED',CHKP:'IMF PORTWATCH (AIS)',MINR:'USGS MCS',SCRN:'COVERAGE INDEX',SANC:'TRADE.GOV CSL',MACR:'FRED · NY FED',WGLB:'IMF PORTWATCH · GEO',HELP:'OPEN SOURCE'};
let activeRun=null,loadToken=0,stressTimer,searchTimer,suggestions=[],suggestIndex=-1,globe=null,globeHost=null,globeMountToken=0,globeMountQueue=Promise.resolve();

function syncURL(){const p=new URLSearchParams(location.search);if(state.ticker)p.set('t',state.ticker);else p.delete('t');if(state.fn)p.set('f',state.fn);else p.delete('f');for(const k of ['taiwan','china_export','hormuz','malacca'])p.set(k,String(state.scenario[k]));p.set('h',String(state.scenario.horizon));history.replaceState(null,'',`${location.pathname}?${p}`)}
function isLanding(){return !state.ticker;}
function showView(animate=false){
  const update=async()=>{const landing=isLanding();$('landing').hidden=!landing;$('grid').hidden=landing;$('welcome').hidden=true;$('main').classList.toggle('is-landing',landing);document.body.classList.toggle('landing-mode',landing);if(landing)renderLanding();await mountGlobe(true);};
  if(animate&&document.startViewTransition&&!matchMedia('(prefers-reduced-motion: reduce)').matches)document.startViewTransition(update);
  else update();
}
function renderShell(){
  $('rail').innerHTML=FUNCTIONS.map(([code,title,kr],i)=>`<button data-fn="${code}" class="${state.fn===code?'active':''}" title="${esc(title)} · ${esc(kr)}"><span class="fkey">${i<12?`F${i+1}`:'·'}</span><span class="rail-label"><small>${code}</small><em>${esc(kr)}</em></span></button>`).join('');
  $('grid').innerHTML=layouts.map(code=>{const meta=FUNCTIONS.find(x=>x[0]===code);return `<section class="panel" data-panel="${code}" id="panel-${code.replace('4','four')}"><div class="panel-head"><b>${code}</b><span>${esc(meta[1])}</span><span class="kr">${esc(meta[2])}</span><span class="source" data-source="${code}">${sources[code]}</span><button data-max="${code}" aria-label="Maximize ${code}">⤢</button></div><div class="panel-body"></div></section>`}).join('');
  renderAll();showView();
}
function renderPanel(code){
  const panel=document.querySelector(`[data-panel="${code}"]`);if(!panel)return;
  const badge=panel.querySelector('.source');
  if(code==='CHKP'&&state.chokepoints)badge.textContent=`IMF PORTWATCH · ${state.chokepoints.source==='live'?'LIVE':'SNAPSHOT'} · ${state.chokepoints.as_of||'—'}`;
  if(code==='MINR'&&state.minerals?.edition)badge.textContent=`USGS · ${state.minerals.edition}`;
  if(code==='STRS')badge.textContent=state.mode==='SNAPSHOT'?'BROWSER REPLICA · SEED 42':state.mode==='MOCK'?'MOCK CAPTURE':'SANDBOX · SERVER';
  if(code==='AGNT'&&state.mode==='SNAPSHOT')badge.textContent=`RECORDED RUN · ${state.result?.generated_at?.replace('T',' ').slice(0,16)||state.index?.generated_at?.replace('T',' ').slice(0,16)||'—'} · engine ${state.result?.engine?.llm||'deterministic'}`;
  const body=panel.querySelector('.panel-body');
  if(code==='DES'&&state.missingTicker){
    const themes=state.index?.themes||[];
    body.innerHTML=`<div class="coverage-empty"><span class="amber">${esc(state.missingTicker)} · OUTSIDE SNAPSHOT</span><h2>Snapshot covers ${state.index?.items?.length||0} KIC-relevant issuers</h2><p>Any SEC registrant runs live when the engine is online — <code>uvicorn app:app</code> locally or Docker.</p><p>스냅샷 수록 종목을 테마별로 선택하세요. 실시간 엔진에서는 SEC 등록 기업을 조회할 수 있습니다.</p>${themes.map(theme=>`<div class="coverage-theme"><h3>${esc(theme)}</h3><div>${(state.index?.items||[]).filter(x=>x.theme===theme).map(x=>`<button data-ticker="${esc(x.ticker)}">${esc(x.ticker)}</button>`).join('')}</div></div>`).join('')}</div>`;
    return;
  }
  if(code==='WGLB'){if(!body.querySelector('#globe-host'))body.innerHTML=renders.WGLB();if(!isLanding())mountGlobe();return;}
  body.innerHTML=renders[code](state);
  if(code==='RISK'&&state.selectedMetric){const detail=body.querySelector('.metric-detail,.hint');if(detail)detail.outerHTML=renderRiskWiring(state);}
  if(code==='MEMO'&&state.result?.memo){const claims=body.querySelector('.claims');if(claims)claims.outerHTML=renderMemoWiring(state);const memo=state.result.memo;const citedText=[memo.summary,...(memo.sections||[]).map(x=>x.body),...(memo.claims||[]).map(x=>x.text),...(memo.hedges||[]).map(x=>x.rationale)].join(' ');const citedIds=[...citedText.matchAll(/\[(E\d+)\]/g)].map(x=>x[1]);const used=[...new Set([...citedIds,...(memo.claims||[]).flatMap(c=>c.evidence_ids||[])])].map(id=>state.evidence.get(id)).filter(Boolean);body.insertAdjacentHTML('beforeend',`<section class="print-evidence"><h3>EVIDENCE APPENDIX · 근거 목록</h3><ol>${used.map(e=>`<li><b>${esc(e.id)}</b> ${esc(e.label)} · ${esc(e.provenance?.title||'')}<br><a href="${esc(e.provenance?.url||'#')}">${esc(e.provenance?.url||'')}</a></li>`).join('')}</ol></section>`);const exportButton=body.querySelector('#export-pdf');if(exportButton&&state.mode!=='LIVE')exportButton.textContent='PRINT / SAVE PDF';}
  if(code==='AGNT'&&state.mode==='SNAPSHOT'){const original=state.index?.scenario||{};const changed=['taiwan','china_export','hormuz','malacca','horizon'].some(k=>Number(original[k])!==Number(state.scenario[k]));if(changed)body.insertAdjacentHTML('afterbegin','<div class="source-note">Recorded run uses the default scenario; STRS recomputes your scenario in the browser.</div>');}
  if(code==='AGNT'&&state.running){const log=$('event-log');if(log)log.scrollTop=log.scrollHeight;}
  if(code==='RISK'||code==='MEMO')requestAnimationFrame(()=>drawEvidenceWires(code));
}
function renderAll(){for(const code of layouts)renderPanel(code);updateFocus();updateTicker();}
function updateFocus(){document.querySelectorAll('.panel').forEach(p=>p.classList.toggle('maximized',p.dataset.panel===state.fn));$('grid').classList.toggle('has-max',!!state.fn);document.querySelectorAll('.rail button').forEach(b=>b.classList.toggle('active',b.dataset.fn===state.fn));globe?.resize?.();}
function setFn(code){state.fn=code;if(!state.ticker&&code!=='SCRN')state.fn=null;updateFocus();syncURL();if(code==='WGLB')mountGlobe();if(code==='SCRN'&&isLanding())$('landing-screener').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'});else window.scrollTo({top:0,behavior:'instant'});}
function notify(message,kind='warn',timeout=6000){const el=$('status');el.textContent=message;el.style.color=kind==='error'?'var(--alert)':'var(--amber)';el.hidden=false;if(timeout)setTimeout(()=>{if(el.textContent===message)el.hidden=true},timeout);}
onLinkStatus(down=>{if(down)notify('DATA LINK DOWN · retrying','warn',0);else if($('status').textContent==='DATA LINK DOWN · retrying')$('status').hidden=true;});
onModeChange(info=>{state.mode=info.mode;state.index=info.index;const b=$('mode-badge');b.hidden=false;b.textContent=info.mode==='SNAPSHOT'?`SNAPSHOT · ${info.index?.generated_at?.replace('T',' ').slice(0,16)||'—'} UTC`:info.mode==='LIVE'?`LIVE · ${info.health?.llm?.enabled?String(info.health.llm.model||'CLAUDE SONNET 5.5').toUpperCase():'DETERMINISTIC'}`:'MOCK · CAPTURE';$('engine').innerHTML=`<i class="led ${info.mode==='LIVE'?'':'offline'}"></i>${info.mode==='LIVE'?'ENGINE: CONNECTED':info.mode==='SNAPSHOT'?'ENGINE: SNAPSHOT':'ENGINE: MOCK'}`;renderPanel('SCRN');renderPanel('STRS');renderPanel('AGNT');renderLanding();});
onLiveChokepoints(data=>{if(state.mode!=='SNAPSHOT'||state.chokepoints?.source==='live'&&state.chokepoints.as_of===data.as_of)return;state.chokepoints=data;renderPanel('CHKP');updateTicker();renderLanding();mountGlobe(true);});
document.addEventListener('mock-fallback',e=>{notify(`MOCK fallback: ${e.detail}`);});

function anomaly(){const items=state.chokepoints?.items||[];return items.filter(x=>Number(x.baseline_365d)>=10&&Number(x.delta_pct)<=-25).sort((a,b)=>(b.baseline_365d-b.mean_7d)-(a.baseline_365d-a.mean_7d))[0]||items.filter(x=>Number(x.baseline_365d)>=10&&Number.isFinite(Number(x.delta_pct))).sort((a,b)=>a.delta_pct-b.delta_pct)[0]||null;}
const koreanChokepoints={'Suez Canal':'수에즈 운하','Panama Canal':'파나마 운하','Bosporus Strait':'보스포루스 해협','Bab el-Mandeb Strait':'바브엘만데브 해협','Malacca Strait':'말라카 해협','Strait of Hormuz':'호르무즈 해협','Cape of Good Hope':'희망봉','Gibraltar Strait':'지브롤터 해협','Dover Strait':'도버 해협','Oresund Strait':'외레순 해협','Taiwan Strait':'대만 해협','Korea Strait':'대한 해협','Tsugaru Strait':'쓰가루 해협','Luzon Strait':'루손 해협','Lombok Strait':'롬복 해협','Ombai Strait':'옴바이 해협','Bohai Strait':'보하이 해협','Torres Strait':'토레스 해협','Sunda Strait':'순다 해협','Makassar Strait':'마카사르 해협','Magellan Strait':'마젤란 해협','Yucatan Channel':'유카탄 해협','Windward Passage':'윈드워드 해협','Mona Passage':'모나 해협','Balabac Strait':'발라박 해협','Bering Strait':'베링 해협','Mindoro Strait':'민도로 해협','Kerch Strait':'케르치 해협'};
function renderLanding(){
  $('landing-screener').innerHTML=renderSCRN(state);
  const x=anomaly();if(!x)return;
  const d=Number(x.delta_pct),mean=Number(x.mean_7d),base=Number(x.baseline_365d),name=x.name;
  const korean=koreanChokepoints[name]||name;
  $('signal-headline').textContent=`${korean} 통항 ${d>=0?'+':''}${d.toFixed(1)}% · 7일 평균 ${mean.toFixed(1)}척/일 vs 365일 평균 ${base.toFixed(1)}척`;
  $('signal-english').textContent=`${name} transits ${d>=0?'+':''}${d.toFixed(1)}% vs 365-day baseline`;
  $('signal-date').textContent=`as of ${state.chokepoints.as_of||x.latest?.date||'—'}`;
  $('signal-source').textContent=`IMF PORTWATCH · ${state.chokepoints.source==='live'?'LIVE':'SNAPSHOT'} ↗`;
  $('signal-source').dataset.chokepoint=x.id;
}
function addChokepointEvidence(x){if(!x?.provenance)return null;const id=`portwatch:${x.id}`,e={id,kind:'number',label:`${x.name} · 7-day daily mean vs 365-day baseline`,value:x.mean_7d,unit:'ships/day',provenance:x.provenance};state.evidence.set(id,e);return id;}

async function loadTicker(raw){
  const ticker=(raw||'').trim().toUpperCase();
  if(!/^[A-Z]{1,6}$/.test(ticker)){notify(`${ticker||'Symbol'} · use an issuer ticker, e.g. NVDA or TSM`);return;}
  await ready;
  if(state.mode==='SNAPSHOT'&&!state.index?.items?.some(x=>x.ticker===ticker)){
    const wasLanding=isLanding();++loadToken;activeRun?.close();activeRun=null;state.ticker=ticker;state.missingTicker=ticker;state.company=null;state.exposure=null;state.stress=null;state.metrics=[];state.result=null;state.events=[];state.evidence.clear();state.selectedMetric=null;state.fn='DES';syncURL();showView(wasLanding);renderAll();return;
  }
  const token=++loadToken,wasLanding=isLanding();activeRun?.close();activeRun=null;state.ticker=ticker;state.missingTicker=null;state.company=null;state.exposure=null;state.stress=null;state.metrics=[];state.result=null;state.events=[];state.evidence.clear();state.selectedMetric=null;
  syncURL();showView(wasLanding);renderAll();
  const calls=[['company',`company/${ticker}`,ticker],['exposure',`exposure/${ticker}`,ticker],['stress',`stress/${ticker}?${scenarioQuery(state.scenario)}`,ticker],['chokepoints','chokepoints'],['minerals','minerals'],['macro','macro']];
  if(state.mode==='SNAPSHOT')calls.push(['analyze',`analyze?ticker=${ticker}`,ticker]);
  await Promise.all(calls.map(async([kind,path,t])=>{
    try{
      const data=await getJSON(kind,path,t);if(token!==loadToken||kind==='chokepoints'&&data.source==='snapshot'&&state.chokepoints?.source==='live')return;
      if(kind==='stress'){state.stress=data.stress;state.metrics=data.metrics||[];}
      else if(kind==='analyze'){state.result=data;state.evidence=new Map((data.evidence||[]).map(e=>[e.id,e]));state.events=[];if(!state.metrics.length)state.metrics=data.metrics||[];if(!state.stress)state.stress=data.stress;}
      else state[kind]=data;
      if(kind==='exposure'&&data.entities_named?.length&&!state.sanctionsQuery){state.sanctionsQuery=data.entities_named[0];screenSanctions(state.sanctionsQuery);}
      for(const code of kind==='company'?['DES']:kind==='exposure'?['4W','CHKP','MINR']:kind==='stress'?['STRS','RISK']:kind==='chokepoints'?['CHKP','WGLB']:kind==='minerals'?['MINR']:kind==='macro'?['MACR']:['RISK','AGNT','MEMO'])renderPanel(code);
      if(kind==='chokepoints'){updateTicker();mountGlobe(true);}if(kind==='exposure')mountGlobe(true);
    }catch(error){if(token===loadToken){console.warn(`${kind}: ${error.message}`);notify(`${kind.toUpperCase()} · ${error.message}`,'error');}}
  }));
}
function updateTicker(){const items=state.chokepoints?.items||[];if(!items.length)return;const html=items.map(c=>`${esc(c.name.toUpperCase())} ${c.mean_7d==null?'—':Number(c.mean_7d).toFixed(1)}/DAY <span class="${c.delta_pct<=-25?'alert':c.delta_pct>=0?'positive':'amber'}">${c.delta_pct>=0?'▲':'▼'}${Math.abs(c.delta_pct||0).toFixed(1)}%</span>`).join('　　');$('ticker-content').innerHTML=`${html}　　${html}`;}
async function loadGlobal(){const tasks=[['chokepoints','chokepoints'],['minerals','minerals'],['macro','macro']];await Promise.all(tasks.map(async([kind,path])=>{try{const data=await getJSON(kind,path);if(kind==='chokepoints'&&data.source==='snapshot'&&state.chokepoints?.source==='live')return;state[kind]=data;renderPanel(kind==='chokepoints'?'CHKP':kind==='minerals'?'MINR':'MACR');if(kind==='chokepoints'){updateTicker();renderLanding();mountGlobe(true);}}catch(error){console.warn(`${kind}: ${error.message}`);}}));}
async function primeSnapshotChokepoints(){
  if(state.mode!=='SNAPSHOT')return;
  try{const response=await fetch('./snapshots/chokepoints.json');if(!response.ok)return;state.chokepoints={...await response.json(),source:'snapshot'};renderPanel('CHKP');updateTicker();renderLanding();mountGlobe(true);}catch{/* live fetch remains the source fallback */}
}
function scheduleStress(){clearTimeout(stressTimer);syncURL();stressTimer=setTimeout(async()=>{if(!state.ticker)return;try{const data=await getJSON('stress',`stress/${state.ticker}?${scenarioQuery(state.scenario)}`,state.ticker);state.stress=data.stress;state.metrics=data.metrics||[];renderPanel('STRS');renderPanel('RISK');}catch(error){console.warn(`stress: ${error.message}`);}},250);}
function startAnalysis(){
  if(!state.ticker){notify('Load a ticker first.');return;}
  activeRun?.close();state.running=true;state.events=[];state.question=$('question')?.value||state.question;renderPanel('AGNT');setFn('AGNT');
  activeRun=analyze(state.ticker,state.scenario,state.question,e=>{if(!e)return;state.events.push(e);renderPanel('AGNT');},result=>{
    state.running=false;activeRun=null;state.result=result;state.company=result.company||state.company;state.exposure=result.exposure||state.exposure;state.stress=result.stress||state.stress;state.metrics=result.metrics||state.metrics;state.evidence=new Map((result.evidence||[]).map(e=>[e.id,e]));state.events=result.events?.length?result.events:state.events;
    if(state.mode==='LIVE'){state.chokepoints={items:result.chokepoints||[]};state.minerals={items:result.minerals||[]};}
    renderAll();mountGlobe(true);notify(`ANALYSIS COMPLETE · ${state.events.length} EVENTS · ${result.evidence?.length||0} EVIDENCE`,'warn',4500);
  },message=>{state.running=false;activeRun=null;renderPanel('AGNT');notify(`ANALYSIS ERROR · ${message}`,'error');});
}
async function screenSanctions(q){q=(q||'').trim();if(!q)return;state.sanctionsQuery=q;renderPanel('SANC');try{state.sanctions=await getJSON('sanctions',`sanctions?q=${encodeURIComponent(q)}`);renderPanel('SANC');}catch(error){console.warn(`sanctions: ${error.message}`);}}
function evidenceOpen(id){const e=state.evidence.get(id);if(!e){notify(`Evidence ${id} is not in the current ledger`);return;}const p=e.provenance||{};$('drawer-content').innerHTML=`<span class="amber">${esc(e.id)}</span><h2>${esc(e.label)}</h2>${e.value!=null?`<div class="e-value">${Number(e.value).toLocaleString('en-US',{maximumFractionDigits:2})} <small>${esc(e.unit||'')}</small></div>`:''}${e.text?`<blockquote>${esc(e.text)}</blockquote>`:''}<dl><dt>SOURCE</dt><dd>${esc(p.source||'—')}</dd><dt>TITLE</dt><dd>${esc(p.title||'—')}</dd><dt>LOCATOR</dt><dd>${esc(p.locator||'—')}</dd><dt>RETRIEVED</dt><dd>${esc(p.retrieved_at||'—')}</dd><dt>LICENSE</dt><dd>${esc(p.license||'—')}</dd></dl><a class="source-link" href="${esc(p.url||'#')}" target="_blank" rel="noopener">OPEN SOURCE ↗</a>`;$('drawer').classList.add('open');$('drawer').setAttribute('aria-hidden','false');$('drawer-scrim').hidden=false;}
function closeDrawer(){$('drawer').classList.remove('open');$('drawer').setAttribute('aria-hidden','true');$('drawer-scrim').hidden=true;}
function command(raw){const v=raw.trim();if(!v)return;const parts=v.toUpperCase().split(/\s+/),first=parts[0],second=parts[1];$('command').value='';$('suggestions').hidden=true;if(first==='HELP'||first==='?'){setFn('HELP');return;}if(first==='RUN'||first==='AGNT'){startAnalysis();return;}if(first==='SANC'&&parts.length>1){screenSanctions(v.slice(5));setFn('SANC');return;}if(first==='HOME'||first==='SIGNAL'){state.ticker='';state.fn=null;syncURL();showView();return;}if(codes.includes(first)){setFn(first);return;}if(second&&codes.includes(second)){const loading=loadTicker(first);state.fn=second;loading.then(()=>{setFn(second);if(second==='AGNT')startAnalysis();});return;}state.fn=null;loadTicker(first);}
function updateSuggestions(){clearTimeout(searchTimer);const q=$('command').value.trim();if(!q||q.includes(' ')){$('suggestions').hidden=true;return;}searchTimer=setTimeout(async()=>{try{suggestions=(await search(q)).slice(0,12);suggestIndex=-1;const el=$('suggestions');el.innerHTML=suggestions.map((x,i)=>`<button data-suggestion="${i}"><b>${esc(x.ticker)}</b><span>${esc(x.name)}</span></button>`).join('');el.hidden=!suggestions.length;}catch{suggestions=[];$('suggestions').hidden=true;}},150);}
function setSuggestion(){const x=suggestions[suggestIndex];if(x){$('command').value=x.ticker;$('suggestions').hidden=true;}}
function highlightSuggestion(){$('suggestions').querySelectorAll('button').forEach((b,i)=>b.classList.toggle('selected',i===suggestIndex));}

function bind(){
  document.addEventListener('click',e=>{
    const el=e.target.closest('[data-evidence],[data-max],[data-fn],[data-metric],[data-screen],[data-sort],[data-suggestion],[data-ticker],[data-scrn-sort]');
    if(el){
      if(el.dataset.evidence)evidenceOpen(el.dataset.evidence);else if(el.dataset.max)setFn(state.fn===el.dataset.max?null:el.dataset.max);else if(el.dataset.fn)setFn(el.dataset.fn);else if(el.dataset.metric){state.selectedMetric=el.dataset.metric;renderPanel('RISK');setFn('RISK');}else if(el.dataset.screen){screenSanctions(el.dataset.screen);setFn('SANC');}else if(el.dataset.sort){state.chkpSort=el.dataset.sort;renderPanel('CHKP');}else if(el.dataset.suggestion!=null){suggestIndex=Number(el.dataset.suggestion);setSuggestion();$('command').focus();}else if(el.dataset.ticker){loadTicker(el.dataset.ticker);}else if(el.dataset.scrnSort){state.scrnDesc=state.scrnSort===el.dataset.scrnSort?!state.scrnDesc:false;state.scrnSort=el.dataset.scrnSort;renderPanel('SCRN');renderLanding();}return;
    }
    if(e.target.id==='signal-source'){const x=anomaly();const id=addChokepointEvidence(x);if(id)evidenceOpen(id);}
    if(e.target.id==='run-analysis')startAnalysis();if(e.target.id==='code-toggle'){state.showCode=!state.showCode;renderPanel('STRS');}if(e.target.id==='sanctions-run')screenSanctions($('sanctions-input').value);if(e.target.id==='export-pdf')exportPDF();if(e.target.id==='share')share();if(e.target.id==='copy-link')copyLink();
  });
  document.addEventListener('input',e=>{if(e.target.id==='command')updateSuggestions();if(e.target.dataset.slider){state.scenario[e.target.dataset.slider]=Number(e.target.value)/100;e.target.nextElementSibling.textContent=`${e.target.value}%`;scheduleStress();}});
  document.addEventListener('change',e=>{if(e.target.id==='horizon'){state.scenario.horizon=Number(e.target.value);scheduleStress();}});
  document.addEventListener('keydown',e=>{
    if(e.key==='Escape'){if($('drawer').classList.contains('open'))closeDrawer();else if(state.fn)setFn(null);else $('command').blur();$('suggestions').hidden=true;return;}
    if(e.key==='/'&&!['INPUT','TEXTAREA'].includes(document.activeElement.tagName)){e.preventDefault();$('command').focus();return;}
    if(e.key.startsWith('F')&&/^F([1-9]|1[0-2])$/.test(e.key)){e.preventDefault();setFn(codes[Number(e.key.slice(1))-1]);return;}
    if(e.altKey&&/^[1-9]$/.test(e.key)){e.preventDefault();setFn(codes[Number(e.key)-1]);return;}
    if(e.target.id==='command'){if(e.key==='ArrowDown'){e.preventDefault();suggestIndex=Math.min(suggestions.length-1,suggestIndex+1);highlightSuggestion();}else if(e.key==='ArrowUp'){e.preventDefault();suggestIndex=Math.max(0,suggestIndex-1);highlightSuggestion();}else if(e.key==='Enter'){e.preventDefault();if(!$('suggestions').hidden&&suggestIndex>=0)setSuggestion();command($('command').value);}}
    else if(e.key==='Enter'&&e.target.id==='sanctions-input')screenSanctions(e.target.value);
    else if((e.key==='Enter'||e.key===' ')&&e.target.dataset.ticker){e.preventDefault();loadTicker(e.target.dataset.ticker);}
  });
  $('drawer-close').onclick=closeDrawer;$('drawer-scrim').onclick=closeDrawer;
}
async function exportPDF(){if(!state.result)return;if(state.mode!=='LIVE'){window.print();return;}try{const response=await fetch(memoURL(),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(state.result)});if(!response.ok)throw new Error(`HTTP ${response.status}`);const url=URL.createObjectURL(await response.blob()),a=document.createElement('a');a.href=url;a.download=`GeoAlphaSentinel_${state.ticker}_memo.pdf`;a.click();setTimeout(()=>URL.revokeObjectURL(url),5000);}catch(error){notify(`PDF export failed · ${error.message}`,'error');}}
function shareText(){return `${state.ticker} Geo-Alpha Sentinel | ${state.metrics.map(m=>`${m.code} ${m.code==='FAR'?(m.rating||m.value):Number(m.value).toFixed(1)}`).join(' · ')}`;}
function shareLink(){const u=new URL(location.href);u.search='';u.searchParams.set('t',state.ticker);return u.href;}
function share(){const current=$('share-menu');if(current){current.remove();return;}const link=shareLink(),title=shareText(),menu=document.createElement('div');menu.id='share-menu';menu.className='share-menu';for(const [label,url] of [['X / TWITTER',`https://twitter.com/intent/tweet?text=${encodeURIComponent(title)}&url=${encodeURIComponent(link)}`],['LINKEDIN',`https://www.linkedin.com/shareArticle?mini=true&url=${encodeURIComponent(link)}&title=${encodeURIComponent(title)}`]]){const a=document.createElement('a');a.textContent=label;a.href=url;a.target='_blank';a.rel='noopener';menu.appendChild(a);}$('share').after(menu);}
async function copyLink(){const link=shareLink();try{await navigator.clipboard.writeText(link);notify('LINK COPIED');}catch{notify(link);}}
function clocks(){const time=z=>new Intl.DateTimeFormat('en-GB',{timeZone:z,hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date());$('clocks').textContent=`NYC ${time('America/New_York')}  SEL ${time('Asia/Seoul')}  UTC ${time('UTC')}`;}
function mountGlobe(refresh=false){
  const host=isLanding()?$('signal-globe'):$('globe-host');if(!host)return;
  if(globe&&globeHost===host&&!refresh){globe.resize?.();return;}
  const token=++globeMountToken;globeMountQueue=globeMountQueue.catch(()=>{}).then(async()=>{
    if(token!==globeMountToken)return;if(globe){globe.destroy?.();globe=null;}
    try{const module=await import('./globe.js');if(token!==globeMountToken||!document.body.contains(host))return;const instance=await module.mountGlobe(host,state,{onChokepoint:c=>{const id=addChokepointEvidence(c);if(id)evidenceOpen(id);},focus:anomaly()});if(token!==globeMountToken){instance.destroy?.();return;}globe=instance;globeHost=host;}
    catch(error){if(token!==globeMountToken)return;console.warn(`globe: ${error.message}`);host.innerHTML='<div class="empty">GLOBE UNAVAILABLE<small>Geographic signal pending</small></div>';}
  });return globeMountQueue;
}
function drawEvidenceWires(code){drawWires(document.querySelector(`[data-panel="${code}"]`));}
async function init(){renderShell();bind();clocks();setInterval(clocks,30000);await ready;state.index=await getIndex();renderPanel('SCRN');renderLanding();$('chips').innerHTML=(state.index.items||[]).slice(0,6).map(x=>`<button data-ticker="${esc(x.ticker)}">${esc(x.ticker)}</button>`).join('');await primeSnapshotChokepoints();if(state.ticker)loadTicker(state.ticker);else loadGlobal();syncURL();}
init().catch(error=>{console.error(error);notify(`STARTUP · ${error.message}`,'error',0);});
