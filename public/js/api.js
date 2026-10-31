const params = new URLSearchParams(location.search);
export const mock = params.get('mock') === '1';
const statusListeners = new Set(), modeListeners = new Set(), chokepointListeners = new Set(), cache = new Map();
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const mockNames = {search:'search_nv.json',chokepoints:'chokepoints.json',minerals:'minerals.json',macro:'macro.json',sanctions:'sanctions_huawei.json'};
let config = {api_base:'',snapshot_base:'./snapshots'}, snapshotApi = null, index = null;
export let mode = 'CONNECTING', modeInfo = {mode,health:null,index:null};
export function onLinkStatus(fn) { statusListeners.add(fn); return () => statusListeners.delete(fn); }
export function onModeChange(fn) { modeListeners.add(fn); if(mode!=='CONNECTING')fn(modeInfo); return () => modeListeners.delete(fn); }
export function onLiveChokepoints(fn) { chokepointListeners.add(fn); return () => chokepointListeners.delete(fn); }
function link(down) { for(const fn of statusListeners)fn(down); }
function announce() { modeInfo={mode,health:modeInfo.health,index}; for(const fn of modeListeners)fn(modeInfo); }
function snapURL(path) { return `${config.snapshot_base.replace(/\/$/,'')}/${path}`; }
function liveURL(path) { return config.api_base ? new URL(`api/v1/${path}`,`${config.api_base.replace(/\/$/,'')}/`).href : new URL(`./api/v1/${path}`,document.baseURI).href; }
async function json(url,options={}) { const r=await fetch(url,options); if(!r.ok)throw new Error(`HTTP ${r.status} · ${url}`); return r.json(); }
async function loadIndex() { if(!index)index=mode==='SNAPSHOT'&&snapshotApi?.index?await snapshotApi.index():await json(snapURL('index.json')); return index; }
export async function getIndex() { await ready; return index||loadIndex(); }
export const ready = (async()=>{
  if(!mock) {
    try { config={...config,...await json('./config.json')}; } catch { /* documented defaults */ }
    if(params.has('api'))config.api_base=params.get('api')||'';
    const controller=new AbortController(), timeout=setTimeout(()=>controller.abort(),2500);
    try { const health=await json(liveURL('health'),{signal:controller.signal,headers:{Accept:'application/json'}}); if(health.status!=='ok')throw new Error('No live engine'); modeInfo.health=health; mode='LIVE'; }
    catch { mode='SNAPSHOT'; } finally { clearTimeout(timeout); }
  } else mode='MOCK';
  if(mode!=='LIVE') {
    try { const module=await import('./snapshot.js'); snapshotApi=module.createSnapshotApi({snapshotBase:config.snapshot_base}); }
    catch { snapshotApi=null; }
  }
  try { await loadIndex(); } catch { index={generated_at:null,scenario:null,themes:[],items:[]}; }
  announce(); return modeInfo;
})();
function mockFile(kind,ticker) { return kind==='analyze'?`analyze_${ticker||'NVDA'}.events.json`:mockNames[kind]||`${kind}_${ticker||'NVDA'}.json`; }
async function fromMock(kind,ticker,signal) {
  let file=mockFile(kind,ticker), response=await fetch(`./mock/${file}`,{signal});
  if(!response.ok&&ticker&&ticker!=='NVDA') { file=mockFile(kind,'NVDA'); response=await fetch(`./mock/${file}`,{signal}); if(response.ok)document.dispatchEvent(new CustomEvent('mock-fallback',{detail:`${ticker} → NVDA ${kind}`})); }
  if(!response.ok)throw new Error(`${file} unavailable`); return response.json();
}
async function fromLive(path,signal) {
  let wait=400; for(let attempt=0;attempt<3;attempt++) {
    try { const data=await json(liveURL(path),{signal,headers:{Accept:'application/json'}}); link(false); return data; }
    catch(error) { if(signal?.aborted)throw error; link(true); if(attempt===2)throw error; await delay(wait); wait*=2; }
  }
}
async function analysis(ticker) { const key=`${ticker}/analysis.json`; if(!cache.has(key))cache.set(key,json(snapURL(key))); return cache.get(key); }
async function fromSnapshot(kind,path,ticker) {
  if(kind==='health')return snapshotApi?.health?snapshotApi.health():{status:'snapshot',llm:{enabled:false,model:'deterministic'}};
  if(kind==='search') { const q=new URLSearchParams(path.split('?')[1]||'').get('q')||''; return snapshotApi?.search ? snapshotApi.search(q) : (await loadIndex()).items.filter(x=>x.ticker.toLowerCase().startsWith(q.toLowerCase())||x.name.toLowerCase().includes(q.toLowerCase())).slice(0,12).map(x=>({ticker:x.ticker,name:x.name,cik:''})); }
  if(snapshotApi) {
    const method={company:'company',exposure:'exposure',chokepoints:'chokepoints',minerals:'minerals',macro:'macro',sanctions:'sanctions',stress:'stress'}[kind];
    if(method&&typeof snapshotApi[method]==='function') {
      if(kind==='company'||kind==='exposure')return snapshotApi[method](ticker);
      if(kind==='sanctions')return snapshotApi[method](new URLSearchParams(path.split('?')[1]||'').get('q')||'');
      if(kind==='stress')return snapshotApi[method](ticker,Object.fromEntries(new URLSearchParams(path.split('?')[1]||'')),{exactAtSnapshotScenario:true});
      if(kind==='chokepoints')return snapshotApi.chokepoints({live:true,onLive:data=>{for(const fn of chokepointListeners)fn(data);}});
      return snapshotApi[method]();
    }
  }
  if(kind==='company'||kind==='exposure')return json(snapURL(`${ticker}/${kind}.json`));
  if(kind==='chokepoints'||kind==='minerals'||kind==='macro')return json(snapURL(`${kind}.json`));
  if(kind==='stress') {
    const a=await analysis(ticker), scenario=Object.fromEntries(new URLSearchParams(path.split('?')[1]||'')), defaults=(await loadIndex()).scenario||{};
    if(Object.keys(defaults).every(k=>k==='question'||scenario[k]==null||Number(scenario[k])===Number(defaults[k])))return {stress:a.stress,metrics:a.metrics};
    const {runStressReplica,recomputeScenarioMetrics}=await import('./stress_replica.js');
    const stress=runStressReplica({...a.stress.inputs,__code:a.stress.code},scenario);
    return {stress,metrics:recomputeScenarioMetrics(a.metrics,stress)};
  }
  if(kind==='analyze')return snapshotApi?.analysis?snapshotApi.analysis(ticker):analysis(ticker);
  throw new Error(`${kind} unavailable in snapshot`);
}
export async function getJSON(kind,path,ticker,{signal}={}) { await ready; if(mode==='MOCK')return fromMock(kind,ticker,signal); if(mode==='LIVE')return fromLive(path,signal); return fromSnapshot(kind,path,ticker); }
export async function search(q) { return getJSON('search',`search?q=${encodeURIComponent(q)}`); }
export function scenarioQuery(s) { return new URLSearchParams({taiwan:s.taiwan,china_export:s.china_export,hormuz:s.hormuz,malacca:s.malacca,horizon:s.horizon}).toString(); }
export function analyze(ticker,scenario,question,onEvent,onResult,onError) {
  let closed=false,stream=null,replay=null;
  (async()=>{
    await ready; if(closed)return;
    if(mode==='LIVE') {
      const qs=scenarioQuery(scenario)+`&ticker=${encodeURIComponent(ticker)}&question=${encodeURIComponent(question||'')}`;
      stream=new EventSource(liveURL(`analyze?${qs}`));
      stream.addEventListener('agent',e=>{if(!closed)onEvent(JSON.parse(e.data));});
      stream.addEventListener('result',e=>{stream.close();if(!closed)onResult(JSON.parse(e.data));});
      stream.addEventListener('error',e=>{stream.close();if(!closed)onError(e.data?JSON.parse(e.data).message:'Stream disconnected');}); return;
    }
    if(mode==='SNAPSHOT'&&snapshotApi?.analyzeReplay) {
      replay=snapshotApi.analyzeReplay(ticker,{onEvent:e=>{if(!closed)onEvent(e);},onResult:r=>{if(!closed)onResult(r);},onError:e=>{if(!closed)onError(e?.message||String(e));}});
      return;
    }
    const payload=mode==='MOCK'?await fromMock('analyze',ticker):await analysis(ticker);
    const rows=Array.isArray(payload)?payload:(payload.events||[]).map(e=>({event:'agent',data:e})).concat([{event:'result',data:payload}]);
    let prev=0,total=0;
    for(const row of rows) {
      if(closed)return; const type=row.event||'agent',data=typeof row.data==='string'?JSON.parse(row.data):row.data,t=Number(row.t_ms??data?.t_ms??prev);
      const gap=Math.min(400,Math.max(0,t-prev));prev=t;total+=gap;await delay(Math.min(gap,Math.max(0,12000-total)));if(closed)return;
      if(type==='result')onResult(data);else if(type==='error')onError(data?.message||'Analysis failed');else onEvent(data);
    }
  })().catch(error=>{if(!closed)onError(error.message)});
  return {close(){closed=true;stream?.close();replay?.close?.();}};
}
export function memoURL() { return liveURL('memo.pdf'); }
