let state=null, running=false;
const launchErrors={};
function render(){
 running=state.running;$('#run').disabled=running;$('#run').textContent=running?'Calcul en cours…':'Lancer les backtests ↗';
 const entries=Object.entries(state.strategies);$('#count').textContent=`${entries.length} stratégies · portefeuille indépendant pour chacune`;
 $('#rows').innerHTML=entries.map(([key,row])=>{const s=row.result?.summary;return `<tr><td class="name">${esc(row.name)}<small>${esc(key)}</small></td><td><span class="status ${row.status==='error'?'error':''}">${statuses[row.status]||esc(row.status)}</span>${row.error?`<small class="error" style="display:block;white-space:normal;max-width:230px">${esc(row.error)}</small>`:''}</td><td class="${s?.pnl>=0?'positive':'negative'}">${s?money(s.pnl):'—'}</td><td>${s?pct(s.return):'—'}</td><td>${s?pct(s.max_drawdown):'—'}</td><td>${s&&s.closed_trades?pct(s.win_rate):'—'}</td><td>${s?s.closed_trades:'—'}</td><td>${s?.rejected_orders??'—'}</td><td><span class="status ${row.eligibility?.status==='eligible'?'positive':''}">${eligibilityLabels[row.eligibility?.status||'not_tested']}</span>${launchErrors[key]?`<div class="launch-error error" role="alert" style="white-space:normal;max-width:280px;margin-top:8px">${esc(launchErrors[key])}</div>`:''}<br><button class="secondary start-paper" data-key="${esc(key)}" ${row.eligibility?.status==='eligible'&&!running?'':'disabled'} style="margin-top:8px">Démarrer paper</button>${row.eligibility?.status!=='eligible'?`<br><button class="secondary start-paper manual-paper" data-key="${esc(key)}" data-manual="true" ${row.status==='done'&&row.eligibility?.status!=='not_tested'&&!running?'':'disabled'} style="margin-top:8px">Lancer manuellement</button>`:''}</td><td>${s?`<a class="secondary more" href="/strategy?name=${encodeURIComponent(key)}" target="_blank" rel="noopener noreferrer" aria-label="Détails de ${esc(row.name)} dans un nouvel onglet">More ↗</a>`:'—'}</td></tr>`}).join('');
 document.querySelectorAll('.start-paper').forEach(button=>button.onclick=async()=>{
  button.disabled=true;
  delete launchErrors[button.dataset.key];
  try{const response=await fetch('/api/paper/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({strategy:button.dataset.key,manual:button.dataset.manual==='true'})});const data=await response.json();if(!response.ok)throw Error(data.error);location.href='/paper';}
  catch(error){
   launchErrors[button.dataset.key]=error.message;
   $('#message').textContent=error.message;
   let notice=button.parentElement.querySelector('.launch-error');
   if(!notice){notice=document.createElement('div');notice.className='launch-error error';notice.setAttribute('role','alert');notice.style.cssText='white-space:normal;max-width:280px;margin-top:8px';button.parentElement.appendChild(notice);}
   notice.textContent=error.message;
   button.disabled=false;
 }
 });
 const p=state.policy;
 $('#policy').textContent=`Filtre paper : rendement net > ${pct(p.min_return)}, drawdown ≤ ${pct(p.max_drawdown)}, au moins ${p.min_closed_trades} trades clôturés au total et ${p.min_validation_trades} sur les ${Math.round(p.validation_fraction*100)} % les plus récents. Détails des critères dans More.`;
 const d=state.dataset,c=state.config;
 if(d){$('#dataset').innerHTML=`${esc(date(d.start))} → ${esc(date(d.end))} · <strong>${d.candles.toLocaleString('fr-FR')} bougies</strong> · Capital ${money(c.capital)} · Position ${pct(c.position_size)} · Frais ${pct(c.fee_rate)} · Slippage ${pct(c.slippage_rate)}${d.gaps?`<br><span class="error">Historique discontinu : ${d.gaps} trou(s), environ ${d.missing_candles} bougies manquantes. Aucune interpolation : des signaux et variations intermédiaires peuvent manquer. Compléter l’historique avant de conclure.</span>`:''}`;}
 if(d&&c.market_rules){const r=c.market_rules;$('#dataset').innerHTML+=`<br>Règles Kraken actuelles du ${esc(date(r.fetched_at))} : minimum ${esc(r.ordermin)} BTC et ${money(Number(r.costmin))} hors frais · ${r.lot_decimals} décimales de quantité. Appliquées à toute la période historique.`;}
 else if(!d) $('#dataset').textContent=running?'Chargement et vérification des données…':'Lancez les backtests pour analyser les données locales.';
}
async function load(){const response=await fetch('/api/results');if(!response.ok)throw Error('Serveur indisponible');state=await response.json();render()}
$('#config').onsubmit=async e=>{e.preventDefault();$('#message').textContent='';$('#run').disabled=true;const data=new FormData(e.target),options={refresh:data.has('refresh'),repair:data.has('repair')};for(const k of ['capital','position_size','fee_rate','slippage_rate'])options[k]=Number(data.get(k))/(k==='capital'?1:100);try{const r=await fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(options)});const body=await r.json();if(!r.ok)throw Error(body.error||'Un calcul est déjà en cours.');await load()}catch(err){$('#message').textContent=err.message;$('#run').disabled=running}};
async function poll(){try{await load()}catch(err){$('#message').textContent='Connexion au dashboard interrompue. Vérifiez que le serveur Python tourne.'}setTimeout(poll,2000)}poll();