let state=null;
const selected=new URLSearchParams(location.search).get('name');
function detail(){
 const panel=$('#detail'), row=state?.strategies[selected];
 if(!row?.result){panel.hidden=false;panel.textContent=row?.error || 'Aucun résultat disponible. Lancez les backtests depuis la liste.';return}
 panel.hidden=false;
 const r=row.result,s=r.summary;
 const metrics=[['Portefeuille final',money(s.final_equity)],['P&L réalisé',money(s.realized_pnl)],['P&L latent',money(s.unrealized_pnl)],['Frais cumulés',money(s.fees)]];
 panel.innerHTML=`<div class="section-title"><h2>${esc(row.name)}</h2><a class="secondary" href="/">Liste des stratégies ↗</a></div><p>${esc(row.description)}</p><div class="stats">${metrics.map(([k,v])=>`<div class="stat"><label>${k}</label><strong>${v}</strong></div>`).join('')}</div><div class="section-title"><span class="chart-key">● Valeur du portefeuille · EUR</span><span class="sub">Position finale : ${s.open_position?'ouverte':'aucune'} · Buy & hold brut, capital entier : ${pct(s.buy_hold_return)}</span></div>${chart(r.equity_curve)}<h2 style="margin-top:24px">Transactions simulées <span class="sub">(${r.trades.length})</span></h2><div class="table-wrap trades"><table><thead><tr><th>Date d’exécution</th><th>Ordre</th><th>Prix</th><th>Quantité BTC</th><th>Frais</th><th>Cash après ordre</th></tr></thead><tbody>${r.trades.map(t=>`<tr><td>${esc(date(t.timestamp))}</td><td class="${t.side==='BUY'?'positive':''}">${esc(t.side)}</td><td>${money(t.price)}</td><td>${t.quantity.toFixed(8)}</td><td>${money(t.fee)}</td><td>${money(t.capital)}</td></tr>`).join('')||'<tr><td colspan="6" class="empty">Aucune transaction sur cette période.</td></tr>'}</tbody></table></div>`;
 document.title = `Neo — ${row.name}`;
}

async function loadStrategy(){
 try {
  const response=await fetch('/api/results');
  if(!response.ok)throw Error('Impossible de charger les résultats.');
  state=await response.json();
  const d=state.dataset,c=state.config;
  $('#context').textContent=d?`${date(d.start)} → ${date(d.end)} · ${d.candles} bougies · Capital ${money(c.capital)} · Position ${pct(c.position_size)} · Frais ${pct(c.fee_rate)} · Slippage ${pct(c.slippage_rate)}`:'';
  $('#warning').textContent=d?.gaps?`Historique discontinu : ${d.gaps} trou(s), environ ${d.missing_candles} bougies manquantes. Les résultats sont à interpréter avec cette limite.`:'';
  if(!Object.hasOwn(state.strategies,selected))throw Error('Stratégie introuvable.');
  detail();
  if(state.running&&!state.strategies[selected].result)setTimeout(loadStrategy,2000);
 }catch(error){$('#detail').hidden=false;$('#detail').textContent=error.message;}
}
loadStrategy();
