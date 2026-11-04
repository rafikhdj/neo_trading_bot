# PR #2 — preuve documentaire de ClosedOrders

Vérification le **4 novembre 2026**, uniquement par lecture des pages publiques
Kraken. Aucun appel privé, aucune clé, aucun ordre envoyé. Cette note apporte
une preuve de contrat publié, **pas une validation empirique en production**.

## Références exactes

[ClosedOrders, page officielle](https://docs.kraken.com/api-reference/account-data/get-closed-orders)
et sa [version Markdown avec OpenAPI embarqué](https://docs.kraken.com/api-reference/account-data/get-closed-orders.md).
Dans ce dernier document :

- Opération : `paths./private/ClosedOrders.post`, `operationId: getClosedOrders`.
- Corps : `components.requestBodies.closedOrders.content.application/json.schema.properties`.
  Les propriétés `cl_ord_id` (string), `with_cursor` (boolean), `cursor` (string)
  et `trades` (boolean) sont définies dans ce même objet. `ofs` est déprécié
  et ne doit pas être non nul avec `with_cursor=true`.
- Réponse : `components.schemas.closed-2.properties.result.properties.cursor`
  référence `components.schemas.cursor`. Son `next` est une chaîne nullable ;
  l'objet peut être absent ou nul en dernière page. `count=0` est normal en
  mode curseur et ne signifie donc pas que la recherche est terminée.
- Le filtre client porte sur les ordres clôturés ; aucun statut IOC n'est exclu
  dans le contrat. Les IDs de trades restent conditionnels à leur disponibilité.

[AddOrder](https://docs.kraken.com/api-reference/trading/add-order) et sa
[version Markdown/OpenAPI](https://docs.kraken.com/api-reference/trading/add-order.md)
définissent `cl_ord_id` et `timeinforce=IOC`. L'IOC peut être exécuté immédiatement,
avec annulation du reliquat. L'unicité client garantie concerne les ordres ouverts,
pas une idempotence permanente des tentatives de création.

Interprétation pour notre reprise : interroger ClosedOrders avec le même UUID
après perte de réponse est conforme au contrat publié, y compris si l'ordre
n'est déjà plus ouvert. **Cela ne garantit ni visibilité immédiate ni présence
systématique de l'ID dans la réponse.** Un résultat absent/incomplet n'autorise
jamais à renvoyer AddOrder ; l'intention reste bloquée.

## Requête réellement testée

Premier appel : `cl_ord_id=<UUID>`, `with_cursor=true`, `trades=true` et nonce
signé. Appels suivants : mêmes paramètres, plus `cursor=<next>` reçu. Aucun
`ofs`, `start` ou `end` n'est injecté. La recherche continue même avec `count=0`,
jusqu'à la dernière page ; curseur incohérent/répété ou plafond de 1000 pages :
blocage. Le test vérifie les paramètres form-encodés réellement transmis au mock.

Les tests de reprise créent une intention SQLite `unknown` sans txid connu,
retournent OpenOrders vide puis un IOC clôturé dans ClosedOrders (rempli, ou
partiellement rempli puis annulé). La réconciliation enregistre txid/exécutions,
sans appel AddOrder et sans activation. Ce sont des **tests synthétiques du
moteur**, pas une expérimentation sur l'exchange.

## Ligne historique sans identifiant client

La requête est filtrée, pas une exploration de tout l'historique du compte.
Le code bloque pourtant si une ligne retournée n'a pas `cl_ord_id`, même si elle
semble ancienne ou sans rapport, et même si une autre ligne correspond à l'UUID.
Ce choix conservateur est maintenu : sans ID client ni txid durable, il est
impossible de prouver qu'elle est sans rapport et d'établir une association unique.

Le test couvre la ligne anonyme avant **et après** un candidat valable. Aucun
retour anticipé du candidat ne contourne le blocage. Une ligne portant un autre
ID client explicite peut être ignorée, car cette différence est vérifiable.
Le compte dédié neuf limite ces situations mais ne constitue pas une preuve ;
ce blocage peut également se produire sur un compte neuf. Il nécessite la
[procédure de résolution sûre](live_trading.md#procédure-pour-un-ordre-qui-reste-incertain).
Aucune association par montant, aucun effacement de journal, aucun retry aveugle.

## Traçabilité des sources

SHA-256 des contenus Markdown publics récupérés le 4 novembre 2026 :

- ClosedOrders : `f1bd03932475e870314999af1d382f4f7c93b872cd3ec2e79cb244d09a293c77`
- AddOrder : `1d69666f71881fdaae1d85812325ed27095cb8652cef9919e02569c9e48f9513`

Ces empreintes identifient le contenu consulté, pas sa pérennité ni la conformité
future du service. Une validation supervisée du compte/exchange demeure requise
avant tout premier ordre explicitement autorisé par le propriétaire.
