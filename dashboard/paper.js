const selected=new URLSearchParams(location.search).get('name');
let revision='';
function tableTrades(state){
 return `<h2>Transactions simulées (${state.trades.length})</h2><div class="table-wrap trades"><table><thead><tr><th>Date</th><th>Ordre</th><th>Prix</th><th>Quantité BTC</th><th>Frais</th></tr></thead><tbody>${state.trades.map(t=>`<tr><td>${esc(date(t.timestamp))}</td><td>${esc(t.side)}</td><td>${money(t.price)}</td><td>${t.quantity.toFixed(8)}</td><td>${money(t.fee)}</td></tr>`).join('')||'<tr><td colspan="5" class="empty">Aucune transaction pour le moment.</td></tr>'}</tbody></table></div>`;
}
function render(portfolios){
 const label=p=>p.status==='running'?'En cours':'En pause';
 const action=(key,p)=>`<button class="secondary control" data-key="${esc(key)}" data-action="${p.status==='running'?'pause':'start'}">${p.status==='running'?'Pause':'Reprendre'}</button>`;
 if(selected){
  const p=portfolios[selected];
  if(!Object.hasOwn(portfolios,selected)){$('#content').innerHTML='<p>Portefeuille introuvable.</p><a class="secondary" href="/paper">Liste Paper</a>';return}
  const s=p.state,m=p.summary;
  const rejected=s.rejected_orders||[];
  const cards=[['Portefeuille',money(s.equity)],['P&L net',money(s.pnl)],['Cash',money(s.cash)],['BTC',s.quantity.toFixed(8)]];
  $('#content').innerHTML=`<section class="panel"><div class="section-title"><h2>${esc(p.name)}</h2><div class="actions"><span class="badge">${label(p)}</span>${action(selected,p)}<a class="secondary" href="/paper">Liste Paper ↗</a></div></div><p class="sub">Début : ${esc(date(p.started_at))} · Dernière bougie traitée : ${s.updated_at?esc(date(s.updated_at)):'en attente'}.</p>${p.error?`<p class="error">${esc(p.error)}</p>`:''}${p.message?`<p class="sub">${esc(p.message)}</p>`:''}<p class="sub">Capital initial ${money(s.config.capital)} · Position ${pct(s.config.position_size)} · Frais ${pct(s.config.fee_rate)} · Slippage ${pct(s.config.slippage_rate)}.</p><div class="stats">${cards.map(([k,v])=>`<div class="stat"><label>${k}</label><strong>${v}</strong></div>`).join('')}</div><p class="sub">Rendement ${pct(m.return)} · Drawdown ${pct(m.max_drawdown)} · Win rate ${m.closed_trades?pct(m.win_rate):'—'} · ${m.closed_trades} trades clôturés · Frais ${money(m.fees)} · ${m.rejected_orders} tentatives refusées.</p>${s.equity_curve?.length>1?chart(s.equity_curve):'<p class="empty">La courbe apparaîtra après le traitement d’une nouvelle bougie.</p>'}${tableTrades(s)}${rejected.length?`<h2 style="margin-top:24px">Tentatives refusées (${rejected.length})</h2><div class="table-wrap trades"><table><thead><tr><th>Date</th><th>Ordre</th><th>Motif</th></tr></thead><tbody>${rejected.map(t=>`<tr><td>${esc(date(t.timestamp))}</td><td>${esc(t.side)}</td><td>${esc(t.reason)}</td></tr>`).join('')}</tbody></table></div>`:''}<h2 style="margin-top:24px">Validation au lancement</h2><p class="sub">Rendement minimum > ${pct(p.approval.policy.min_return)} ; drawdown ≤ ${pct(p.approval.policy.max_drawdown)}. Le code de la stratégie est lié à cette validation : s’il change, le paper se met en pause.</p></section>`;
 }else{
  const entries=Object.entries(portfolios);
  $('#content').innerHTML=entries.length?`<section class="panel" style="padding:0"><div class="table-wrap"><table><thead><tr><th>Stratégie</th><th>Statut</th><th>Portefeuille</th><th>P&L net</th><th>Rendement</th><th>Drawdown</th><th>Trades clôturés</th><th>Dernière mise à jour</th><th></th></tr></thead><tbody>${entries.map(([key,p])=>`<tr><td class="name">${esc(p.name)}${p.error?`<small class="error">${esc(p.error)}</small>`:(p.message?`<small>${esc(p.message)}</small>`:'')}</td><td>${label(p)}</td><td>${money(p.state.equity)}</td><td class="${p.state.pnl>=0?'positive':'negative'}">${money(p.state.pnl)}</td><td>${pct(p.summary.return)}</td><td>${pct(p.summary.max_drawdown)}</td><td>${p.summary.closed_trades}</td><td>${p.state.updated_at?esc(date(p.state.updated_at)):'En attente'}</td><td><div class="actions">${action(key,p)}<a class="secondary" href="/paper?name=${encodeURIComponent(key)}" target="_blank" rel="noopener noreferrer">More ↗</a></div></td></tr>`).join('')}</tbody></table></div></section>`:'<section class="panel empty"><h2>Aucune stratégie en paper</h2><p>Lancez les backtests, puis utilisez « Démarrer paper » sur une stratégie éligible.<br>Les stratégies sans assez de trades restent en attente ; les critères non atteints sont expliqués dans More.</p><a class="secondary" href="/">Voir les backtests ↗</a></section>';
 }
 document.querySelectorAll('.control').forEach(b=>b.onclick=async()=>{
  b.disabled=true;
  try{const r=await fetch('/api/paper/'+b.dataset.action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({strategy:b.dataset.key})});const body=await r.json();if(!r.ok)throw Error(body.error);revision='';await load();}
  catch(e){$('#message').textContent=e.message;b.disabled=false}
 });
}
async function load(){const r=await fetch('/api/paper');if(!r.ok)throw Error('Serveur indisponible');const data=await r.json(),next=JSON.stringify(data);if(next!==revision){render(data);revision=next}}
async function poll(){try{await load()}catch(e){$('#message').textContent=e.message}setTimeout(poll,2000)}poll();
