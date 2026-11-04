# Fixtures synthétiques, aucun compte réel

`query_closed_without_client_id.json` suit les propriétés et la structure des
exemples/schémas officiels Kraken Spot REST (consultés le 3 novembre 2026).
Les identifiants et montants sont inventés. Ce fichier n'est pas une capture
réelle ni une preuve d'intégration Kraken.

- [QueryOrders](https://docs.kraken.com/api-reference/account-data/query-orders-info) :
  `cl_ord_id` optionnel/nullable ; `descr.price` = prix primaire de l'ordre,
  `price` à la racine = prix moyen exécuté. Les trades ne sont disponibles que
  si demandés **et si les données sont disponibles**.
- [ClosedOrders](https://docs.kraken.com/api-reference/account-data/get-closed-orders) :
  filtre `cl_ord_id`, `with_cursor`, `cursor` ; `result.cursor` présent seulement
  s'il reste des résultats, peut être null. `next` est une chaîne nullable.
- [OpenOrders](https://docs.kraken.com/api-reference/account-data/get-open-orders) :
  même caractère optionnel de l'identifiant client et des trades.

Les tests dérivent les variantes absentes/nulles, partielles, contradictoires et
malformées de ces données. Les cas où le contrat ne garantit pas la disponibilité
des champs bloquent la réconciliation plutôt que d'inventer une association.
