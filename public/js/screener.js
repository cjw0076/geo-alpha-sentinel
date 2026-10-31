import {esc} from './panels.js';
const columns=[['ticker','TICKER'],['name','COMPANY'],['cpi','CPI'],['gpi','GPI'],['srs','SRS'],['far','FAR'],['recommendation','STANCE'],['rev_shock_p05','REV P05']];
const num=v=>v==null?'—':Number(v).toFixed(1);
export function renderSCRN(s){
  const items=s.index?.items||[];
  if(!items.length)return '<div class="empty">COVERAGE INDEX LOADING</div>';
  const sort=s.scrnSort||'ticker',sign=s.scrnDesc?-1:1;
  const themes=(s.index.themes||[]).map(t=>typeof t==='string'?t:t.name).filter(Boolean);
  const ordered=[...new Set([...themes,...items.map(x=>x.theme)])];
  const compare=(a,b)=>sign*(typeof a[sort]==='number'?a[sort]-b[sort]:String(a[sort]??'').localeCompare(String(b[sort]??'')));
  const rows=ordered.map(theme=>{
    const group=items.filter(x=>x.theme===theme).sort(compare);
    if(!group.length)return '';
    return `<tr class="theme-row"><th colspan="8">${esc(theme.toUpperCase())} <small>· ${group.length} ISSUERS</small></th></tr>${group.map(x=>`<tr class="click-row screener-row" data-ticker="${esc(x.ticker)}" tabindex="0" aria-label="Load ${esc(x.ticker)}"><th class="amber">${esc(x.ticker)}</th><td>${esc(x.name)}</td><td class="numeric">${num(x.cpi)}</td><td class="numeric">${num(x.gpi)}</td><td class="numeric">${num(x.srs)}</td><td>${esc(x.far||'—')}</td><td><span class="rec rec-${esc(String(x.recommendation||'').toLowerCase())}">${esc(x.recommendation||'—')}</span></td><td class="numeric">${x.rev_shock_p05==null?'—':num(x.rev_shock_p05)+'%'}</td></tr>`).join('')}`;
  }).join('');
  return `<div class="screener-head"><strong>${items.length} ISSUERS · ${ordered.length} THEMES</strong><span>SNAPSHOT ${esc(s.index.generated_at?.replace('T',' ').slice(0,16)||'—')} UTC</span></div><div class="table-scroll screener-scroll"><table class="screener-table"><thead><tr>${columns.map(([key,label])=>`<th><button data-scrn-sort="${key}" aria-label="Sort by ${label}">${label}${sort===key?s.scrnDesc?' ↓':' ↑':''}</button></th>`).join('')}</tr></thead><tbody>${rows}</tbody></table></div>`;
}
