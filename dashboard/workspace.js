/* Projection de présentation uniquement : aucune décision de trading. */
function paperStatus(p){return {running:'En cours',paused:'En pause'}[p.status]||'État non reconnu'}
function liveFor(key,live){return live?.journals.filter(j=>j.strategy===key)||[]}
function strategyMatches(key,row,filter,query,paper,live){
 const search=`${row.name||key} ${key}`.normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase();
 const needle=query.normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase().trim();
 return search.includes(needle)&&(filter==='all'||(filter==='paper'&&paper&&Object.hasOwn(paper,key))||
  (filter==='live'&&liveFor(key,live).length>0)||(filter==='untested'&&(!row.result||row.status!=='done'||row.eligibility?.status==='not_tested')));
}
function environmentBadges(key,paper,live){
 const p=paper&&Object.hasOwn(paper,key)?paper[key]:null;
 return `${p?`<span class="badge">Paper · ${esc(paperStatus(p))}</span>`:''}${liveFor(key,live).length?'<span class="badge">Live · journal enregistré</span>':''}${!p&&!liveFor(key,live).length?'<span class="sub">Lab</span>':''}${paper===null||live===null?'<small class="sub">Appartenance non entièrement vérifiée</small>':''}`;
}
function strategyOverview(row){
 const d=row.definition,n=d?.notes;
 return `<section class="panel"><div class="section-title"><h2>La stratégie</h2><span class="badge">${esc(n?.cadence||'Documentation à vérifier')}</span></div>
 <p>${esc(d?.description||row.description||'Description non disponible pour cette version.')}</p>
 ${n?`<div class="logic-grid"><div><h3>Entrée LONG</h3><p>${esc(n.entry)}</p></div><div><h3>Sortie / FLAT</h3><p>${esc(n.exit)}</p></div></div><h3>Formules</h3><div class="formulas">${n.formulas.map(f=>`<code>${esc(f)}</code>`).join('')}</div><p class="sub">C : clôture · H/L : plus haut / plus bas · V : volume · indices sur bougies entièrement connues.</p>`:'<p class="notice">Documentation mathématique non validée pour cette version du code. Aucune formule déduite ou inventée.</p>'}
 <h3>Paramètres de la version actuelle</h3><dl class="parameters">${Object.entries({...d?.constants,...d?.defaults}).map(([k,v])=>`<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join('')||'<p class="sub">Aucun paramètre numérique exposé.</p>'}</dl><p class="sub">Valeurs par défaut du code actuel, non éditables ici. Elles ne remplacent pas la configuration enregistrée d’un portefeuille paper ou d’un journal live.</p>
 <h3>Limites</h3><p>${esc(n?.limits||'Consulter et vérifier la version actuelle du module.')}</p><p class="sub">Signaux sur clôtures, exécution backtest à l’ouverture suivante. Frais et slippage fixes ; pas de carnet, latence ni remplissage partiel simulé. Les résultats passés ne garantissent aucune performance.</p></section>`;
}
function contextMenu(key,row,paper,running){
 const p=paper&&Object.hasOwn(paper,key)?paper[key]:null;
 const eligible=row.eligibility?.status==='eligible';
 const manual=row.status==='done'&&row.eligibility?.status!=='not_tested';
 return `<details class="context-menu" data-menu="${esc(key)}"><summary aria-label="Actions pour ${esc(row.name||key)}">⋯</summary><div class="menu-items">
 ${p?`<a href="/paper?name=${encodeURIComponent(key)}">Consulter le paper</a><button class="start-paper" data-key="${esc(key)}" data-action="${p.status==='running'?'pause':'start'}" data-manual="${p.approval?.manual===true}" ${!['running','paused'].includes(p.status)||running?'disabled':''}>${p.status==='running'?'Mettre le paper en pause':'Reprendre le paper'}</button>`:
 `<button class="start-paper" data-key="${esc(key)}" data-action="start" ${!paper||!eligible||running?'disabled':''}>Démarrer en paper</button>${!eligible?`<button class="start-paper" data-key="${esc(key)}" data-action="start" data-manual="true" ${!paper||!manual||running?'disabled':''}>Lancer le paper manuellement</button>`:''}`}
 <a href="/live?name=${encodeURIComponent(key)}">Consulter les journaux live</a></div></details>`;
}
function wireMenus(){
 document.querySelectorAll('.context-menu').forEach(menu=>{
  menu.addEventListener('toggle',()=>{if(menu.open){document.querySelectorAll('.context-menu').forEach(other=>{if(other!==menu)other.open=false});menu.classList.remove('menu-below');if(menu.querySelector('.menu-items').getBoundingClientRect().top<8)menu.classList.add('menu-below')}});
  menu.addEventListener('keydown',event=>{if(event.key==='Escape'){menu.open=false;menu.querySelector('summary').focus()}});
 });
}
