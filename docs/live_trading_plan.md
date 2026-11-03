# Plan MVP — live Kraken Spot BTC/EUR

**Document de préparation uniquement. Aucun ordre réel ni endpoint d'écriture
n'est ajouté dans cette mission. La clé et le client actuels restent read-only.**
Le paper reste un simulateur local ; ses soldes et prix simulés ne deviennent
jamais ceux du compte réel.

## Périmètre et activation

Un seul exchange, BTC/EUR spot, LONG/FLAT, sans levier. Premier déploiement :
**une seule stratégie live et un seul processus**, idéalement sur un compte dédié,
pour ne pas mélanger les BTC du bot avec des achats manuels ou d'autres bots.
Pas de WebSocket, de HFT ni d'onglet live. REST suffit à ce MVP sur clôtures.

Un futur module d'exécution séparé réutilisera l'authentification Kraken. Le
client actuel conservera sa liste de trois endpoints de lecture. Au démarrage,
le live sera toujours **désactivé**, indépendamment de l'état précédemment sauvé.
L'opérateur devra activer explicitement une session après réconciliation et
contrôle du risque. Une nouvelle version de stratégie imposera une nouvelle
validation. Toute extension des permissions devra faire l'objet d'une étape
ultérieure explicite ; aucune modification de clé maintenant.

## Intentions et cycle des ordres — à développer ensuite

Avant tout futur envoi, enregistrer durablement une intention avec : compte,
version de stratégie, paire, bougie du signal, sens, quantité et limites de risque.
Cette clé métier est unique : rejouer le même signal ne crée pas une autre intention.
Associer un `cl_ord_id` Kraken unique à cette intention, puis le `txid` fourni
par Kraken. La contrainte [documentée par Kraken](https://docs.kraken.com/api-reference/trading/add-order)
porte sur les ordres ouverts : ne pas en déduire une idempotence des réessais,
notamment après clôture. Le journal doit conserver l'unicité au-delà de cette durée.

Journal proposé pour le futur live : un fichier SQLite via la bibliothèque
standard, contraintes uniques sur la clé métier, l'ID client et les IDs
d'exécution. Il n'affectera pas les fichiers JSON du paper. Un seul ordre en cours
à la fois, montants réservés inclus dans les plafonds avant l'envoi.

États minimum : `prepared`, `sending`, `unknown`, `open`, `partially_filled`,
`filled`, `rejected`, `canceled`. Persister `sending` **avant** l'appel réseau.
La réponse d'acceptation confirme un ordre, pas une exécution. Actualiser cash,
BTC et frais uniquement à partir des exécutions confirmées, en dédupliquant
leurs IDs. Une annulation peut laisser une quantité déjà exécutée : ne pas
restituer fictivement cette partie au cash.

**Timeout, déconnexion ou arrêt après envoi :** classer l'intention `unknown`.
Ne jamais refaire aveuglément l'appel de création, même avec le même ID client.
Bloquer les nouvelles opérations, chercher l'ordre dans les ordres ouverts,
clôturés et les exécutions ; retrouver le `txid` depuis l'ID client si nécessaire.
Les futures lectures ciblées et la pagination devront être ajoutées et testées
à cette étape. Si l'absence ou le résultat reste incertain, intervention manuelle.
Une erreur de sauvegarde après envoi suit la même procédure : aucune nouvelle
opération tant que l'état durable et Kraken ne sont pas conciliés.

## Réconciliation

Kraken est la source de vérité des soldes, des réservations et des exécutions.
Au démarrage, après incident et avant réactivation : lire soldes, ordres ouverts,
ordres clôturés pertinents et exécutions ; rapprocher le journal local de Kraken.
Ne pas se limiter aux 50 derniers ordres du client actuel. Prévoir une lecture
ciblée/paginée et les informations nécessaires aux montants réellement libres :
`Balance` seul ne suffit pas à représenter les réservations.

Vérifier les totaux BTC/EUR, frais et quantités exécutées avec des tolérances
explicites liées aux précisions de la paire. Dépôt, retrait, ordre manuel ou
ordre inconnu doit être signalé, pas imputé à la performance de la stratégie.
Divergence, ordre `unknown`, historique incomplet ou lecture impossible :
**rester désactivé**, expliquer le motif et exiger une résolution puis une
réactivation manuelle. Une reprise ne rejoue jamais les anciennes intentions.

## Risque et arrêt

Avant chaque intention :

- Données clôturées récentes et API disponible ; pas de signal basé sur une
  période manquante. Arrêter les nouveaux envois si l'une des deux est indisponible.
- Capital autorisé fixe en EUR, plafond du montant d'un ordre et plafond du
  capital total engagé, en comptant les ordres ouverts et frais estimés.
  Taille = minimum du budget configuré, du solde réellement libre et des plafonds.
- Règles Kraken actualisées : paire autorisée, minimum de quantité, minimum de
  montant, précision du volume ; tick de prix si un ordre limite est retenu.
  Arrondir vers le bas et refuser plutôt qu'augmenter automatiquement le risque.
- Jamais de vente au-delà des BTC réellement disponibles ; aucune position short.
- Limite de perte de session/journée en EUR et limite de drawdown définies avant
  activation, calculées depuis un état Kraken réconcilié, frais inclus.

Un **kill switch manuel ou automatique** bloque immédiatement les nouvelles
créations d'ordre et désactive la session. Une fois l'état connu, une procédure
future pourra annuler les ordres encore ouverts, puis vérifier leur état final.
Un arrêt ne garantit ni l'annulation ni la liquidation lorsque l'API est coupée.
Ne pas transformer automatiquement « arrêter » en vente au marché ; une réduction
des positions doit avoir une politique distincte et explicite.

## Limites de la persistance paper actuelle

Les incidents et pauses sont ajoutés aux états JSON atomiques. Un échec d'écriture
met la stratégie en pause ; si l'état ne peut être confirmé, la reprise exige une
relecture au redémarrage après réparation du disque. Si aucune écriture n'est
possible, l'incident peut rester uniquement dans les logs et la mémoire : aucune
garantie de persistance physique dans ce cas. Les fichiers et verrous restent
mono-processus, sans garantie de durabilité face à toute panne matérielle.
Cela ne remplace pas le futur journal d'ordres ni la réconciliation Kraken.

## Conditions avant le premier ordre réel

Tester en environnement de test Kraken accessible au compte : acceptation,
refus, remplissage partiel, annulation concurrente avec exécution, timeout après
acceptation, crash après envoi et panne disque. Sans cet accès, les mocks restent
utiles mais ne prouvent pas l'intégration de l'exchange.

Il reste à construire et vérifier le journal durable, la réconciliation complète,
la politique de prix/slippage, les plafonds chiffrés et le kill switch. Un Mac en
veille ne surveille pas les ordres : choisir un hôte disponible et supervisé.
La gestion actuelle du nonce est mono-processus ; conserver une clé dédiée et
une horloge synchronisée. Évaluer les frais réels et la liquidité, non seulement
les hypothèses du backtest. Valider aussi un verrou empêchant deux processus de piloter le même compte.
Le premier essai réel, hors de cette mission, devra
faire l'objet d'une activation explicite avec un capital plafonné minimal.

Références Kraken : [ordres et identifiants](https://docs.kraken.com/api-reference/trading/add-order),
[ordres ouverts](https://docs.kraken.com/api-reference/account-data/get-open-orders),
[soldes](https://docs.kraken.com/api-reference/account-data/get-account-balance).
