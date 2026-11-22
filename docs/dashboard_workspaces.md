# Dashboard : espaces de travail

Le serveur Python et ses fichiers HTML/CSS/JS existants restent utilisés, sans
framework frontend ni changement des moteurs de trading.

## Navigation et catalogue

- **Strategy Lab** (`/`) : catalogue compact, résultats des backtests existants,
  recherche par nom/identifiant insensible à la casse et aux accents, filtres.
  La configuration de simulation reste accessible dans un panneau repliable.
- **Paper Trading** (`/paper`) : uniquement les portefeuilles retournés par
  `/api/paper`, avec leurs vrais statuts `running` / `paused`, erreurs, métriques
  et transactions. Aucune stratégie n'y est ajoutée par simple éligibilité.
- **Live Trading** (`/live`) : consultation des seuls journaux SQLite réels
  découverts dans `live_trading/state`. Aucun bouton d'activation, d'ordre ou
  d'annulation, aucun appel Kraken.

Les boutons par ligne sont remplacés par **Détails** (nouvel onglet, comme
auparavant) et un menu natif dépliable,
utilisable au clavier (Échap pour fermer). Les actions paper utilisent exactement
les API et contrôles de validation existants. Le dashboard ne contourne pas le
serveur : démarrage manuel seulement après un backtest courant selon le mécanisme
existant. Les messages d'échec restent visibles. Aucun nouveau seuil de risque.

Les listes deviennent des cartes à deux colonnes sur petits écrans. Les
transactions conservent leur tableau défilable. Les filtres ne lancent aucun
calcul ni ordre :

- Toutes : catalogue et stratégies mentionnées par les états consultables.
- En paper : portefeuille enregistré, en cours **ou en pause**.
- En live : journal réel enregistré, **pas une assertion de moteur actif**.
- À tester : résultat absent/non terminé ou validation marquée `not_tested` par
  le serveur (par exemple après changement de source).

Si une API d'état est indisponible, le filtre correspondant est désactivé et
l'appartenance non entièrement vérifiée est indiquée. Un affichage partiel des
journaux est signalé ; les chiffres ne représentent alors que les données lisibles.
Les métriques absentes sont affichées par un tiret, pas remplacées par zéro.

## Ce que le live permet réellement d'observer

Le moteur live ne persiste pas son état d'activation en mémoire. **SQLite ne
permet pas de dire qu'une stratégie tourne, est activée, ou est arrêtée.** Le
nouvel endpoint de présentation `GET /api/live` retourne donc
`execution_status_observable=false`, et `execution_status=null` par journal.
L'interface affiche « Activité non observable ». Elle ne transforme pas la
présence d'un ordre ou d'un journal en statut d'exécution.

La projection ouvre les fichiers `.sqlite`, `.sqlite3`, `.db` de ce seul dossier
avec SQLite `mode=ro` et une transaction de lecture cohérente (WAL inclus).
Elle n'importe ni client Kraken, ni moteur live, ni classe Journal. Connexions
fermées après lecture ; pas d'initialisation, de migration, d'enrôlement ou de
réconciliation. Aucune baseline ou donnée utilisateur n'est modifiée.

L'identité de journal doit être reconnue et son indicateur réel exactement true ;
les démos dry-run sont exclues. Les formats inconnus/corrompus sont signalés,
sans déduire une absence de stratégie de leur échec. La vue affiche les statuts
d'ordre persistés et leurs exécutions enregistrées, pas un statut supposé chez
Kraken à l'instant présent. Les IDs client/exchange et le fingerprint de compte
ne sont pas exposés par cette projection.

**Limites :** pas de découverte des journaux placés à un autre chemin via `--db`,
pas de heartbeat ou suivi d'activation, pas d'equity/P&L live reconstruits,
pas de contrôles opérationnels live. L'absence de journal dans ce dossier ne
prouve pas l'absence d'une activité ailleurs. Cette fonctionnalité reste une
vue locale en lecture, pas un dashboard d'exécution.

## Fiche stratégie

`/strategy?name=...` montre description, logique d'entrée/sortie, formules
mathématiques en texte, paramètres par défaut/constantes de source, limites,
résultats existants, courbe, transactions et refus d'ordre. La documentation
reste disponible même sans résultat de backtest. Aucun Sharpe ni autre chiffre
non fourni par le rapport existant n'est fabriqué.

Les notes des douze stratégies sont dans `dashboard/strategy_notes.json`. Elles
sont liées au SHA-256 du module lu lors de leur rédaction. Si la source change,
la note et ses formules ne sont plus exposées comme valides : un message demande
une vérification documentaire. Les paramètres sont lus dans la signature/les
constantes actuelles, sans exécution d'un signal ni modification des globals.
Une nouvelle stratégie sans note affiche explicitement cette absence.

Ces paramètres sont ceux de la **version actuelle du code**, pas une preuve des
paramètres d'un ancien run ou portefeuille. Les configurations de backtest/paper
existantes restent affichées séparément. Le dashboard ne consomme pas encore les
résultats JSON individuels de `run_experiment` en dehors du rapport batch existant.
Les interfaces d'exécution ou d'optimisation de paramètres sont hors périmètre.

## Validation

```sh
python -m pytest test/test_dashboard.py test/test_dashboard_workspaces.py -q
python -m pytest -q
node --check dashboard/index.js
node --check dashboard/workspace.js
node --check dashboard/live.js
node --check dashboard/paper.js
node --check dashboard/strategy.js
```

Les nouveaux tests créent des journaux SQLite temporaires et des serveurs
éphémères sans démarrer de boucle paper. Ils couvrent séparation réel/dry-run,
lecture concurrente WAL, absence de mutation, formats invalides, métadonnées
liées au hash, routes GET et refus d'une action live, filtres/recherche et
échappement HTML. Node est nécessaire pour le test JS sans dépendance.

Vérification navigateur ponctuelle avec Playwright installé hors du dépôt et
Chrome local : filtres, recherche, accès aux détails, menu clavier, vue live,
absence d'erreurs JS et de débordement horizontal sur les quatre pages en
1440 px et 390 px. Fixtures isolées marquées TEST UI ; aucun portefeuille
utilisateur n'est chargé, aucun ordre n'est envoyé. Le serveur de prévisualisation
est arrêté après vérification. Aucune dépendance de production ajoutée.

Résultats : **23 tests dashboard réussis**, dont 15 nouveaux cas. Suite complète :
**383 réussis, 10 échecs préexistants inchangés**, tous dans
`test/test_metrics.py::test_invalid_equity_raises_value_error` :
`negative-intermediate-equity`, `negative-equity-after-zero`,
`nan-initial-equity`, `nan-intermediate-equity`, `nan-final-equity`,
`positive-infinite-initial-equity`, `positive-infinite-intermediate-equity`,
`negative-infinite-intermediate-equity`, `positive-infinite-final-equity`,
`negative-infinite-final-equity`. Aucun test exclu. Les moteurs, stratégies et
règles de trading ne font pas partie du diff de cette livraison.
