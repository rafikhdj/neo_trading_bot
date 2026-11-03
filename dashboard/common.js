const $=s=>document.querySelector(s), money=n=>new Intl.NumberFormat('fr-FR',{style:'currency',currency:'EUR',maximumFractionDigits:2}).format(n), pct=n=>new Intl.NumberFormat('fr-FR',{style:'percent',maximumFractionDigits:2}).format(n), date=t=>new Date(t).toLocaleString('fr-FR'), esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const statuses={idle:'À lancer',pending:'En attente',running:'En cours…',done:'Terminé',error:'Erreur'};
function chart(points){
 const values=points.map(p=>p.equity), low=Math.min(...values), high=Math.max(...values), range=high-low||1;
 const x=i=>65+i/(Math.max(points.length-1,1))*880, y=v=>205-(v-low)/range*175;
 const line=values.map((v,i)=>`${x(i)},${y(v)}`).join(' ');
 return `<svg class="chart" viewBox="0 0 980 250" role="img" aria-label="Évolution du portefeuille en euros"><line x1="65" y1="205" x2="945" y2="205" stroke="#30383b"/><line x1="65" y1="30" x2="945" y2="30" stroke="#30383b"/><text x="0" y="35">${high.toFixed(2)} €</text><text x="0" y="210">${low.toFixed(2)} €</text><polyline points="${line}" fill="none" stroke="#d2f879" stroke-width="2"/><text x="65" y="240">${esc(date(points[0].timestamp))}</text><text x="945" y="240" text-anchor="end">${esc(date(points.at(-1).timestamp))}</text></svg>`;
}
