let state=null,paper=null,live=null,running=false,filter='all',revision='';
const launchErrors={};
function render(){
 if(!state)return;
 running=state.running;$('#run').disabled=running;$('#run').textContent=running?'Calcul en cours…':'Lancer les backtests';
 const entries=new Map(Object.entries(state.strategies));
 Object.entries(paper||{}).forEach(([key,p])=>{if(!entries.has(key))entries.set(key,{name:p.name})});
 (live?.journals||[]).forEach(j=>{if(!entries.has(j.strategy))entries.set(j.strategy,{name:j.strategy})});
 if((filter==='paper'&&paper===null)||(filter==='live'&&live===null))filter='all';
 const shown=[...entries].filter(([key,row])=>strategyMatches(key,row,filter,$('#search').value,paper,live));
 $('#count').textContent=`${shown.length} / ${entries.size}`;
 $('#workspace-summary').innerHTML=`<div><strong>${entries.size}</strong><span>Stratégies</span></div><div><strong>${paper?Object.keys(paper).length:'—'}</strong><span>Portefeuilles paper</span></div><div><strong>${live?live.journals.length:'—'}</strong><span>Journaux live ${live&&!live.complete?'lisibles':''}</span></div>`;
 $('#sources-message').textContent=paper===null||live===null?'Certains états sont indisponibles : les filtres correspondants sont désactivés.':!live.complete?'Certains journaux live sont illisibles. La liste live est partielle.':'';
 $('#sources-message').hidden=!$('#sources-message').textContent;
 document.querySelectorAll('[data-filter]').forEach(b=>{b.disabled=(b.dataset.filter==='paper'&&paper===null)||(b.dataset.filter==='live'&&live===null);b.setAttribute('aria-pressed',String(b.dataset.filter===filter))});
 const open=[...document.querySelectorAll('.context-menu[open]')].map(e=>e.dataset.menu);
 $('#rows').innerHTML=shown.map(([key,row])=>{const s=row.result?.summary;return `<tr><td data-label="Stratégie" class="name">${esc(row.name||key)}<small>${esc(key)}</small></td><td data-label="Backtest"><span class="status ${row.status==='error'?'error':''}">${esc(statuses[row.status]||'Non disponible')}</span>${row.error?`<small class="error row-note">${esc(row.error)}</small>`:''}<small class="row-note">${esc(eligibilityLabels[row.eligibility?.status]||'Validation non disponible')}</small></td><td data-label="Environnement"><div class="environment">${environmentBadges(key,paper,live)}</div></td><td data-label="P&L net" class="${s?(s.pnl>=0?'positive':'negative'):''}">${s?money(s.pnl):'—'}<small class="row-note">${s?`${s.closed_trades} clôtures · ${s.closed_trades?pct(s.win_rate):'—'} gagnants`:''}</small></td><td data-label="Rendement">${s?pct(s.return):'—'}</td><td data-label="Drawdown">${s?pct(s.max_drawdown):'—'}</td><td data-label="Actions"><div class="actions"><a class="secondary" href="/strategy?name=${encodeURIComponent(key)}" target="_blank" rel="noopener noreferrer" title="Ouvrir la fiche dans un nouvel onglet">Détails</a>${contextMenu(key,row,paper,running)}</div>${launchErrors[key]?`<small class="error row-note" role="alert">${esc(launchErrors[key])}</small>`:''}</td></tr>`}).join('')||'<tr><td colspan="7" class="empty">Aucune stratégie ne correspond aux filtres.</td></tr>';
 document.querySelectorAll('.context-menu').forEach(e=>{e.open=open.includes(e.dataset.menu)});wireMenus();
 document.querySelectorAll('.start-paper').forEach(button=>button.onclick=async()=>{
  button.disabled=true;delete launchErrors[button.dataset.key];
  try{const response=await fetch('/api/paper/'+button.dataset.action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({strategy:button.dataset.key,manual:button.dataset.manual==='true'})});const data=await response.json();if(!response.ok)throw Error(data.error);location.href='/paper';}
  catch(error){launchErrors[button.dataset.key]=error.message;$('#message').textContent=error.message;render()}
 });
 const p=state.policy;
 $('#policy').textContent=`Filtre paper : rendement > ${pct(p.min_return)}, drawdown ≤ ${pct(p.max_drawdown)}, ${p.min_closed_trades} clôtures au total et ${p.min_validation_trades} en validation. Le menu permet un lancement manuel selon les droits existants.`;
 const d=state.dataset,c=state.config;
 $('#dataset').textContent=d?`${date(d.start)} → ${date(d.end)} · ${d.candles.toLocaleString('fr-FR')} bougies · Capital ${money(c.capital)} · Frais ${pct(c.fee_rate)} · Slippage ${pct(c.slippage_rate)}${d.gaps?` · Historique discontinu : ${d.gaps} trou(s), ${d.missing_candles} bougies manquantes.`:''}`:'Aucun historique de backtest chargé.';
}
async function getJSON(url){const response=await fetch(url);if(!response.ok)throw Error('Source indisponible');return response.json()}
async function load(){
 const results=await Promise.allSettled([getJSON('/api/results'),getJSON('/api/paper'),getJSON('/api/live')]);
 if(results[0].status!=='fulfilled')throw Error('Résultats indisponibles. Vérifiez le serveur.');
 state=results[0].value;paper=results[1].status==='fulfilled'?results[1].value:null;live=results[2].status==='fulfilled'?results[2].value:null;
 const next=JSON.stringify([state,paper,live]);if(next!==revision){revision=next;render()}
}
$('#search').addEventListener('input',render);
 document.querySelectorAll('[data-filter]').forEach(b=>b.onclick=()=>{filter=b.dataset.filter;render()});
$('#config').onsubmit=async e=>{e.preventDefault();$('#message').textContent='';$('#run').disabled=true;const data=new FormData(e.target),options={refresh:data.has('refresh'),repair:data.has('repair')};for(const k of ['capital','position_size','fee_rate','slippage_rate'])options[k]=Number(data.get(k))/(k==='capital'?1:100);try{const r=await fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(options)});const body=await r.json();if(!r.ok)throw Error(body.error||'Un calcul est déjà en cours.');await load()}catch(err){$('#message').textContent=err.message;$('#run').disabled=running}};
async function poll(){try{await load()}catch(error){$('#message').textContent=error.message}setTimeout(poll,2000)}poll();
