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
