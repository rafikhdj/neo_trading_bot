# Live Trading MVP — Kraken Spot BTC/EUR

Le moteur est indépendant du paper. Aucun ordre réel n'a été envoyé pendant
le développement. La CLI ne propose **aucune commande d'activation réelle**.
Le client read-only, les stratégies et le dashboard restent inchangés.

## Essayer sans compte ni réseau

Depuis la racine, avec l'environnement Python du projet activé :

```sh
python -m live_trading demo
python -m live_trading status
python -m live_trading orders
python -m live_trading reconcile
python -m live_trading kill
```

`demo` active explicitement le **faux** client et applique la stratégie existante
`moving_average` à des bougies synthétiques clôturées : achat, vente, frais,
réconciliation et persistance. Ce scénario teste le fonctionnement, pas la
performance de la stratégie. Les prix et remplissages sont simulés.

Le journal par défaut est `live_trading/state/dry_run.sqlite`, ignoré par Git.
Le faux exchange conserve son état dans le fichier voisin `.exchange.json` pour
permettre les tests de reprise sans prendre SQLite comme vérité de l'exchange.
Pour refaire une démo, choisir un autre journal, sans effacer un journal réel :

```sh
python -m live_trading demo --db live_trading/state/demo2.sqlite
python -m live_trading orders --db live_trading/state/demo2.sqlite
```

`status` et `orders` ouvrent SQLite en lecture seule, y compris pendant une
session. Ils n'activent pas de moteur. L'activation est en mémoire uniquement ;
chaque nouvelle instance démarre désactivée. `reconcile` crée une instance
**désactivée** et demande le verrou exclusif du journal.

`kill` crée un fichier `.kill` persistant à côté du journal. Le moteur le vérifie
avant les envois : il arrête les nouvelles opérations, sans vente ni annulation
automatique. Un appel réseau déjà parti peut aboutir. Après résolution de
l'incident, retirer ce fichier manuellement puis réactiver explicitement.

## Brancher une stratégie

`Engine(path, broker, strategy, name, limits=Limits(), warmup=30)` accepte la
fonction existante `strategy(data)` et ses signaux `1 / 0 / -1`. Fournir à
`cycle(data, now=...)` un DataFrame UTC avec `timestamp, open, high, low, close,
volume` ; timestamps d'ouverture espacés de cinq minutes. Fournir uniquement des
bougies clôturées, comme le fait le market data existant. Le moteur refuse une
bougie ouverte, ancienne, des trous dans l'historique ou des valeurs invalides.
Il traite uniquement la dernière clôture, jamais une boucle de rattrapage.

L'activation fixe une borne temporelle : les clôtures antérieures ne sont pas
rejouées. La bougie est consommée durablement avant calcul du signal ; un crash
à ce moment peut abandonner un signal, mais ne le rejoue pas. Une clé SQLite
unique par bougie et un UUID client par intention empêchent les doubles envois.
Le journal est lié à la stratégie, sa version, la clé/compte, le mode et les
limites. Une modification impose une nouvelle validation ; ne pas remplacer le
journal d'un compte réel ayant des positions sans procédure de migration.

Il n'y a pas de daemon ni de branchement automatique des données live : l'appelant
fournit les clôtures et appelle `reconcile()` pour surveiller un ordre en cours.
Aucune modification du paper n'est nécessaire.

## Ordres, portefeuille et reprise

SQLite utilise des transactions, `synchronous=FULL` et un verrou de processus
`flock`. Un seul moteur peut écrire dans un même journal. Ne pas utiliser deux
journaux/processus différents sur le même compte ; ce verrou n'est pas distribué.

L'intention est enregistrée `prepared`, puis `sending` **avant** l'appel réseau.
Une acceptation donne `open`, pas `filled`. Les états sont `prepared`, `sending`,
`unknown`, `open`, `partially_filled`, `filled`, `rejected`, `canceled`.
Seules les exécutions confirmées modifient cash/BTC/frais ; leurs IDs sont uniques.
`valuation(bid)` calcule equity et P&L net, frais inclus.

Après timeout ou sauvegarde incertaine : désactivation, aucun renvoi automatique.
Au redémarrage, `sending` devient `unknown`, `prepared` est abandonné. La
réconciliation cherche les ordres par ID Kraken ou ID client, y compris dans
l'historique clôturé paginé, puis lit leurs exécutions. Un ordre introuvable reste
bloquant ; l'absence dans une page ne prouve jamais qu'il n'a pas existé.
Une erreur persistante de disque ne permet pas de garantir la sauvegarde d'un
incident, mais le processus reste désactivé et `sending` durable permet la reprise.

Les soldes Kraken totaux sont comparés au portefeuille reconstruit, avec des
tolérances EUR `0.0001`, BTC `0.00000001`. Les soldes libres proviennent de
`BalanceEx` et tiennent compte des réservations. Ordre externe, dépôt/retrait,
exécution incomplète ou divergence bloquent les nouvelles opérations. Le premier
raccordement requiert un compte dédié sans BTC ni ordre ouvert. Aucun crédit,
actif tiers, marge, short ni levier n'est accepté.

`cancel(intent_id, confirmation=...)` vise exclusivement un ordre ouvert connu ;
il faut ensuite relire les exécutions et l'état final. Les remplissages déjà
confirmés sont conservés. Une annulation incertaine désactive le moteur.

## Risque et futur client réel

`Limits` configure : capital engagé 100 EUR, montant par ordre 10 EUR, perte
journalière 5 EUR, drawdown 10 %, slippage maximal 0.2 %, réserve frais 1 %,
âge maximal après clôture 90 s, âge maximal du résultat Ticker 30 s. Ce sont des
valeurs de développement à valider avant usage réel. Maximum **un ordre ouvert**,
fixé dans le moteur. Les limites sont appliquées avant chaque envoi.

Les risques de perte/drawdown portent sur le capital alloué et le P&L du compte
dédié ; les points de départ journaliers UTC et le plus haut sont persistants.
Une indisponibilité ou une divergence désactive le moteur. La réconciliation
réussie ne le réactive jamais automatiquement.

Les ordres futurs sont des limites IOC, volume arrondi vers le bas, prix au tick,
minimums Kraken relus, frais préférés en EUR (`fciq`). Le prix borne l'exécution ;
un IOC peut être annulé sans exécution ou n'être que partiellement exécuté.
Le plafond par ordre s'applique aussi aux ventes : une sortie peut nécessiter
plusieurs signaux futurs. Les poussières sous les minimums restent au compte
et ne sont pas liquidées artificiellement.

`KrakenExecutionClient` est séparé du client read-only. Il réutilise uniquement
sa signature/authentification et son transport de session. Les nouvelles clés
seront des variables **exportées**, sans repli sur les clés read-only du `.env` :
`KRAKEN_LIVE_API_KEY`, `KRAKEN_LIVE_API_SECRET`. Ne pas envoyer les valeurs au chat.
Une clé dédiée devra disposer de Query funds, Query open/closed orders & trades,
Create/modify et Cancel/close ; aucun retrait/transfert. Aucune permission
n'a été modifiée ici.

Une vérification réelle **en lecture uniquement**, après configuration volontaire
de ces nouvelles variables, est disponible :

```sh
python -m live_trading reconcile --kraken --db live_trading/state/kraken.sqlite
```

Cette commande ne s'arme pas. L'activation réelle reste une action Python
explicite `engine.activate(confirmation="ENABLE KRAKEN BTC/EUR")`, après
réconciliation et contrôles. Ne pas l'appeler pendant les tests. Le constructeur
seul et `cycle()` sur une instance désactivée n'envoient aucun ordre.

## Tests et limites avant le premier ordre réel

```sh
python -m pytest test/test_live_trading.py test/test_live_kraken.py -q
python -m pytest -q
```

Les nouveaux tests n'utilisent ni réseau ni credentials réels. Les réponses HTTP
Kraken sont simulées. Une validation d'intégration supervisée reste nécessaire
pour les particularités du compte, les frais, les arrondis et la visibilité
éventuellement retardée des exécutions. En cas de retard, le moteur se bloque
plutôt que d'inventer un remplissage. Le Ticker REST n'a pas d'horodatage de
transaction : la fraîcheur mesure la réception HTTP, pas celle de la dernière
transaction sur le marché. Pas de retry automatique ni ordonnanceur de rate limit.

La réconciliation relit actuellement tous les ordres du journal : adaptée à un
petit MVP, elle peut consommer beaucoup de quota à long terme. Pagination bornée
à 1000 pages ; au-delà ou en cas d'historique instable/incomplet, blocage. Aucun
outil de résolution manuelle d'un ordre définitivement introuvable n'est fourni.
Un autre compte, une rotation de clé ou une migration de stratégie demandent une
procédure contrôlée ; ne pas réinitialiser silencieusement l'historique.

Avant le premier ordre : compte dédié, plafond minimal explicitement validé,
hôte disponible et horloge synchronisée, gestion opérationnelle des incidents,
sauvegarde du journal, vérification de l'intégration réelle. Aucun test avec
argent réel ni activation automatique n'a été réalisé dans cette mission.

Références officielles : [AddOrder](https://docs.kraken.com/api-reference/trading/add-order),
[ClosedOrders](https://docs.kraken.com/api-reference/account-data/get-closed-orders),
[QueryTrades](https://docs.kraken.com/api-reference/account-data/query-trades-info),
[BalanceEx](https://docs.kraken.com/api-reference/account-data/get-extended-balance).

## Validation de cette livraison

- Tests live : **45 réussis** (faux exchange et transport HTTP simulé).
- Suite complète, sans exclusion : **220 réussis, 10 échecs préexistants**.
- Même suite sur une extraction propre de `HEAD` avant modifications :
  **175 réussis, les mêmes 10 échecs**. Aucune régression identifiée.
- Démo CLI : deux ordres remplis, deux exécutions, sortie FLAT ; réconciliation
  réussie dans un second processus, moteur toujours désactivé.

Les dix échecs sont dans
`test/test_metrics.py::test_invalid_equity_raises_value_error` :
`negative-intermediate-equity`, `negative-equity-after-zero`,
`nan-initial-equity`, `nan-intermediate-equity`, `nan-final-equity`,
`positive-infinite-initial-equity`, `positive-infinite-intermediate-equity`,
`negative-infinite-intermediate-equity`, `positive-infinite-final-equity`,
`negative-infinite-final-equity`. Ces cas limites du backtester, déjà mis de côté,
n'ont pas été modifiés dans cette mission.

## Suite de la revue PR #2 — contrats Kraken et résolution sûre

Vérification documentaire du 3 novembre 2026, **sans requête privée, clé réelle
ni ordre réel** :

- Les schémas [QueryOrders](https://docs.kraken.com/api-reference/account-data/query-orders-info),
  [OpenOrders](https://docs.kraken.com/api-reference/account-data/get-open-orders)
  et [ClosedOrders](https://docs.kraken.com/api-reference/account-data/get-closed-orders)
  déclarent `cl_ord_id` optionnel/nullable. Il n'est donc pas supposé systématique.
  Avec un `txid` déjà enregistré durablement, une lecture ciblée de ce même ID
  permet de conserver le lien client même si le champ est absent. Une valeur
  contradictoire reste bloquante. Sans `txid` durable ni ID client explicite,
  aucune association n'est déduite du montant, de la quantité ou du seul filtre.
- `ClosedOrders` documente bien `cl_ord_id`, `with_cursor`, `cursor` et
  `result.cursor.next`. Le curseur peut être absent/null quand il ne reste pas
  de page ; c'était un défaut confirmé du premier MVP, maintenant corrigé.
  Un curseur malformé/répété reste bloquant. Les trades sont demandés via
  `trades=true`, mais le schéma précise « if data available » : les volumes,
  coûts, frais et IDs doivent donc être vérifiés, jamais supposés disponibles.
- Pour notre type `limit`, `descr.price` est le prix primaire de l'ordre ; le
  champ `price` à la racine est le prix moyen exécuté. Le moteur vérifie le prix
  primaire contre l'intention durable, le type `limit`, puis chaque prix exécuté
  contre la limite. Un prix manquant/invalide bloque ; aucun repli sur le prix
  moyen. Le schéma ne prouve pas la disponibilité réelle pour tous les états
  IOC d'un compte : cette validation d'intégration reste à faire.
- Aucune erreur API `AddOrder`, y compris `EOrder:*`, n'est considérée comme
  preuve d'absence d'ordre. Elle entraîne `unknown`, désactivation et
  réconciliation. Cela peut nécessiter une intervention même après un refus
  véritable : compromis conservateur assumé. Aucun retry de création.

Les fixtures sous `test/fixtures/kraken/` sont synthétiques et suivent les schémas
publiés ; ce ne sont pas des captures de compte réel.

### Procédure pour un ordre qui reste incertain

1. Activer le kill switch du journal concerné et arrêter son moteur. Ne pas
   renvoyer le signal, effacer le journal, ni créer un autre journal pour reprendre.
2. Sauvegarder SQLite avec son API de backup, ou arrêter/fermer toutes les
   connexions avant copie cohérente ; ne pas copier le seul fichier principal
   pendant une écriture WAL.
3. Consulter Kraken en lecture seule : rechercher les IDs client et Kraken,
   ordres ouverts/clôturés et exécutions. Relancer la réconciliation si les
   données étaient seulement retardées. Conserver les preuves sans secrets.
4. Un `txid` déjà journalisé se réconcilie automatiquement même sans `cl_ord_id`.
   Si le `txid` manque aussi, exiger une preuve fiable de l'association
   intention/ordre (ID client explicite ou confirmation vérifiable de Kraken).
   Ne jamais utiliser la seule proximité du montant ou de l'heure.
5. Aucune commande de réparation forcée n'est livrée. Si une association ne peut
   pas être prouvée, rester bloqué et demander l'assistance de Kraken. Si une
   correction du journal est nécessaire, préparer une réparation ciblée,
   sauvegardée, testée et soumise à revue avant application ; ne pas modifier
   les statuts à la main pour contourner le blocage.
6. Reprise uniquement après réconciliation complète, contrôles de risque
   réussis et nouvelle activation explicitement autorisée par le propriétaire.

### Politique de risque et simulateur après revue

Le **drawdown est historique**, depuis l'enrôlement du journal : son plus haut
n'est pas remis à zéro à minuit. Seule la référence de perte quotidienne change
au passage de date UTC, vers la dernière equity observée ; le gap entre cette
observation et la première du nouveau jour reste compté. Sans observations
continues, ce n'est pas une valorisation exacte à minuit.

Equity allouée = `min(solde EUR initial, capital) + equity du compte - solde EUR
initial` (compte initialement FLAT). Ainsi 1000 EUR au compte avec 100 EUR alloués
ne diluent pas le drawdown : une perte de 10 EUR porte sur 100, pas sur 1000.
Les tests vérifient cette formule et le maintien du plus haut au changement de jour.
Les quatre règles de marché sont converties/validées avant toute comparaison
avec la position, même sur un signal HOLD.

Le simulateur exécute à son prix courant uniquement si celui-ci respecte la
limite : prix favorable ou égal pour l'acheteur/vendeur. Le scénario normal
annule un IOC non négociable ; `partial` exécute une fraction et annule le reste.
Les scénarios `open` et `transient_partial` sont des injections d'état transitoire
pour tester surveillance et incidents ; ils ne prétendent pas représenter un
IOC durable au carnet. Pas de modèle de liquidité ni de latence réelle.

SQLite utilise désormais **WAL + synchronous=FULL**. Un test vérifie qu'une
connexion de lecture voit le dernier commit pendant une transaction d'écriture
exclusive, puis le nouveau commit. Ce mode ne constitue pas un verrou distribué.

Validation après revue : **100 tests live réussis**, suite complète **275 réussis,
10 échecs préexistants inchangés** ; le résultat historique
« 45 » ci-dessus correspond à la livraison initiale et non à cette révision.

## Seconde revue PR #2

La [note de contrat ClosedOrders](kraken_closed_orders_contract.md) donne les
liens officiels, chemins OpenAPI et empreintes des documents consultés le
4 novembre 2026. Elle distingue la compatibilité documentaire de la pagination
et la validation réelle de l'exchange, toujours non réalisée. La politique de
blocage sur une ligne anonyme dans une réponse filtrée est conservée et testée.
Les reprises IOC clôturées après réponse perdue sont testées sans réseau.

Validation de cette seconde revue : **106 tests live réussis** ; suite complète
**281 réussis, les mêmes 10 échecs préexistants** de `test_metrics`. Aucun ordre
réel envoyé et aucune activation ; les chiffres des sections précédentes
retracent les révisions antérieures.
