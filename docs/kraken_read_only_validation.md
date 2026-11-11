# Issue #3 — validation d'intégration Kraken en lecture seule

## Commandes locales

Depuis la racine du dépôt, sans lancer le dashboard ni un moteur :

```sh
.venv/bin/python -m live_trading.verify_read_only --public-only
.venv/bin/python -m live_trading.verify_read_only
```

Le mode public n'utilise aucune clé ni fichier `.env`. Le mode authentifié lit
uniquement `KRAKEN_API_KEY` / `KRAKEN_API_SECRET` depuis l'environnement ou le
`.env` local existant, avec le même chargeur que le client read-only. Les variables
exportées priment. **Aucun repli vers `KRAKEN_LIVE_*`.** Ne jamais transmettre les
valeurs au chat, dans une issue ou dans un argument de commande.

Le premier appel privé est `GetApiKeyInfo`, uniquement pour vérifier les droits.
Avant toute lecture du compte, les trois permissions nécessaires doivent être
présentes : `query-funds`, `query-open-trades`, `query-closed-trades`. Seul
`query-ledger` est également toléré. Tout droit supplémentaire, inconnu ou de
trading/funding, comme toute réponse ambiguë, bloque le diagnostic. Cette
politique stricte peut refuser une clé ayant d'autres droits de consultation.

Dans Kraken, utiliser une clé dédiée avec Funds **Query**, Orders and trades
**Query open orders & trades** et **Query closed orders & trades** uniquement.
Si la clé locale est trop permissive, choisir une clé read-only ou restreindre
ses droits manuellement selon l'usage souhaité. Aucune clé n'est créée ni modifiée
par cette commande ; aucune augmentation de permissions n'est nécessaire.
L'authentification 2FA API/OTP n'est pas implémentée ; son usage restera bloquant.

Sortie JSON assainie : noms d'endpoints, succès/échec de transport, résultats de
contrôle avec codes fixes. Aucun montant, prix, clé, signature, nonce, nom de
compte, IP, curseur ou identifiant d'ordre/exécution n'est affiché. `calls.ok`
signifie réponse transport/API recevable ; le contrôle associé indique ensuite
si le parseur/contrat a été validé. Une erreur distante n'est jamais recopiée.

Codes de sortie : `0` = contrôles publics réussis en mode public, ou tous les
contrôles authentifiés couverts et réussis ; `1` = accès/permissions/schéma en
échec ; `2` = aucune erreur mais couverture incomplète, par exemple compte vide.
`incomplete` ne constitue jamais une autorisation live.

## Ce qui est exécuté

Le diagnostic réutilise `KrakenExecutionClient` : même transport signé et mêmes
parseurs `rules`, `quote`, `balances`, `open_orders`, `_snapshot` et recherche
`find` que la réconciliation live. Une sous-classe restreint son transport à six
endpoints privés de consultation et deux endpoints publics. Les méthodes
`arm`, `submit` et `cancel` sont interdites. Le client privé historique à trois
endpoints reste inchangé.

Aucun `Engine` n'est créé. Aucun SQLite n'est ouvert, ni même temporairement :
il ne s'agit pas d'une comparaison avec le portefeuille d'un journal local.
Ni signal, pause, baseline, incident, état paper/live ni seuil de risque n'est
modifié. Aucun ordre, annulation, daemon ou serveur n'est lancé.

Après validation des permissions :

- `AssetPairs` et `Ticker` : parseurs BTC/EUR et contrôles des règles/prix reçus,
  sans affichage des valeurs.
- `BalanceEx` : parseur des soldes/réservations, sans affichage des valeurs. Les
  restrictions existantes du MVP (actifs tiers/crédit) restent bloquantes.
- `OpenOrders` : parseur exact de la réconciliation. Un ordre d'un type non
  supporté par ce MVP peut bloquer même si Kraken le considère valide.
- `ClosedOrders` : historique non filtré avec `with_cursor=true`, jusqu'à fin
  explicite ; filtre par UUID jamais envoyé pour tester une réponse négative.
- `QueryOrders` : seulement si un véritable ordre est disponible ; `QueryTrades`
  seulement s'il existe des IDs d'exécution réels. Aucun ID fictif n'est utilisé
  pour prétendre vérifier ces accès avec des données.
- Filtre positif `ClosedOrders` via `find` : seulement si un ordre clôturé porte
  réellement un ID client. Le parseur complet d'exécution est alors vérifié.

Un compte et un historique vides sont acceptés proprement ; ils ne prouvent ni
une deuxième page de pagination, ni la disponibilité des trades, ni une reprise
IOC après perte de réponse. Ces contrôles restent explicitement `skipped`.

Par défaut, maximum 5 pages non filtrées ; ajustement local borné :

```sh
.venv/bin/python -m live_trading.verify_read_only --max-pages 10
```

Plafond atteint, doublon d'ordre dans l'historique, curseur invalide/répété,
exécutions manquantes ou divergentes : vérification incomplète/bloquée, jamais
confirmation silencieuse d'un historique partiel. La recherche `find` existante
conserve son plafond indépendant de 1000 pages. Le diagnostic peut relire des
ordres/exécutions ; il n'effectue aucun retry automatique après erreur/rate limit.

## Résultat réel observé le 11 novembre 2026

Commande authentifiée exécutée localement, sortie strictement expurgée :

- **AssetPairs : effectué réellement, OK.** Réponse BTC/EUR acceptée par le parseur
  existant (`status`, `ordermin`, `costmin`, `lot_decimals`, `tick_size` présents).
- **Ticker : effectué réellement, OK.** Réponse acceptée par le parseur `b`/`a` ;
  réception HTTP récente. Cela ne date pas la dernière transaction de marché.
- **GetApiKeyInfo : effectué réellement, réponse API OK.** Le contrôle strict des
  permissions a échoué : `non_read_only_or_unknown_permissions_present`.
  Aucun nom de permission supplémentaire ni métadonnée de clé n'est publié.
- **BalanceEx, OpenOrders, ClosedOrders, QueryOrders, QueryTrades : NON exécutés
  sur Kraken dans cette mission**, car la précondition de permissions a bloqué
  tout accès au compte. Pagination, filtres et données privées restent à valider
  par exécution locale avec une clé strictement read-only.

Le résultat global est **incomplete**, code de sortie `1`. Il ne s'agit pas d'un
échec des parseurs de soldes/ordres, puisqu'ils n'ont pas reçu de réponse réelle.
La seule utilisation authentifiée a servi à lire les permissions de la clé,
puis arrêt avant les lectures du compte. Aucune permission locale n'a été changée.

## Tests hors réseau et limites de preuve

```sh
.venv/bin/python -m pytest test/test_kraken_read_only_validation.py -q
# 27 passed

.venv/bin/python -m pytest test/test_kraken_read_only_validation.py test/test_live_trading.py test/test_live_kraken.py test/test_kraken_private.py -q
# 192 passed

.venv/bin/python -m pytest -q
# 308 passed, 10 failed
```

Les 10 échecs préexistants restent les cas
`test/test_metrics.py::test_invalid_equity_raises_value_error` : equity négative
intermédiaire/après zéro, NaN initial/intermédiaire/final, infini positif
initial/intermédiaire/final et infini négatif intermédiaire/final. Aucun test
n'est masqué ou exclu. Les 106 tests du MVP restent verts.

Les nouveaux tests couvrent garde de transport, permissions inconnues/trop larges,
compte vide, pagination tronquée/ambiguë, données réelles *simulées*, cohérence
des exécutions, absence de journal et de fuite de données. Ils ne prouvent pas
le fonctionnement des endpoints privés sur un compte réel. Les preuves
[documentaires de pagination](kraken_closed_orders_contract.md) restent distinctes.
L'issue suivante sur les agents/stratégies est hors périmètre.

Références officielles :
[GetApiKeyInfo](https://docs.kraken.com/api-reference/account-data/get-api-key-info),
[BalanceEx](https://docs.kraken.com/api-reference/account-data/get-extended-balance),
[ClosedOrders](https://docs.kraken.com/api-reference/account-data/get-closed-orders).
