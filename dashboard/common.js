const $=s=>document.querySelector(s), money=n=>new Intl.NumberFormat('fr-FR',{style:'currency',currency:'EUR',maximumFractionDigits:2}).format(n), pct=n=>new Intl.NumberFormat('fr-FR',{style:'percent',maximumFractionDigits:2}).format(n), date=t=>new Date(t).toLocaleString('fr-FR'), esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const statuses={idle:'À lancer',pending:'En attente',running:'En cours…',done:'Terminé',error:'Erreur'};
function chart(points){
 const values=points.map(p=>p.equity), low=Math.min(...values), high=Math.max(...values), range=high-low||1;
 const width=Math.max(320,Math.min(980,window.innerWidth-80)), right=width-25;
 const x=i=>65+i/(Math.max(points.length-1,1))*(right-65), y=v=>205-(v-low)/range*175;
 const line=values.map((v,i)=>`${x(i)},${y(v)}`).join(' ');
 const axisDate=t=>width<600?new Date(t).toLocaleString('fr-FR',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}):date(t);
 return `<svg class="chart" viewBox="0 0 ${width} 250" role="img" aria-label="Évolution du portefeuille en euros"><line x1="65" y1="205" x2="${right}" y2="205" stroke="#30383b"/><line x1="65" y1="30" x2="${right}" y2="30" stroke="#30383b"/><text x="0" y="35">${high.toFixed(2)} €</text><text x="0" y="210">${low.toFixed(2)} €</text><polyline points="${line}" fill="none" stroke="#d2f879" stroke-width="2"/><text x="65" y="240">${esc(axisDate(points[0].timestamp))}</text><text x="${right}" y="240" text-anchor="end">${esc(axisDate(points.at(-1).timestamp))}</text></svg>`;
}

const eligibilityLabels={not_tested:'À évaluer',insufficient:'Données insuffisantes',rejected:'Refusée',eligible:'Éligible au paper'};
function eligibilityDetails(row){
 const e=row.eligibility||{status:'not_tested',checks:[]},v=row.validation;
 return `<section class="panel"><div class="section-title"><h2>Validation pour le paper</h2><span class="badge ${e.status==='eligible'?'positive':''}">${eligibilityLabels[e.status]}</span></div>
 ${v?`<p class="sub">Validation chronologique : ${esc(date(v.start))} → ${esc(date(v.end))}. Portefeuille neuf, indicateurs amorcés sur les données antérieures.<br>Rendement net : ${pct(v.summary.return)} · Drawdown : ${pct(v.summary.max_drawdown)} · Trades clôturés : ${v.summary.closed_trades}.</p>`:''}
 ${e.reason?`<p>${esc(e.reason)}</p>`:''}
 <div class="table-wrap"><table><thead><tr><th>Critère</th><th>Observé</th><th>Seuil</th><th>Résultat</th></tr></thead><tbody>${e.checks.map(c=>`<tr><td>${esc(c.label)}</td><td>${c.kind==='count'?c.actual:pct(c.actual)}</td><td>${c.kind==='count'?'≥ '+c.required:(c.kind==='return'?'> ':'≤ ')+pct(c.required)}</td><td class="${c.passed?'positive':'negative'}">${c.passed?'OK':'Non atteint'}</td></tr>`).join('')}</tbody></table></div><p class="sub">Ces critères filtrent les stratégies ; ils ne garantissent pas leurs performances futures.</p></section>`;
}
