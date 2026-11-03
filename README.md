# Neo · Bot Kraken

Un fichier par stratégie, un moteur de backtest commun et un dashboard local.
Le dashboard suit les backtests et le paper multi-stratégies. Le live reste
hors périmètre : aucun ordre réel n’est envoyé.

## Installation

Python 3.10 ou plus récent :

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

## Dashboard

Depuis la racine du projet :

```sh
.venv/bin/python -m dashboard
```

Ouvrir **http://127.0.0.1:8000**. Arrêt avec Ctrl+C.
Pour un autre port : `.venv/bin/python -m dashboard --port 8001`.

Le bouton « Lancer les backtests » teste toutes les stratégies :

- Même historique BTC/EUR, bougies de cinq minutes.
- Portefeuille fictif indépendant par stratégie.
- Capital, taille de position, frais et slippage configurables.
- Statuts en attente, en cours, terminé ou erreur.
- P&L net, rendement du portefeuille, drawdown maximum, win rate, trades clôturés et tentatives refusées.
- Lien « More » : nouvel onglet dédié avec courbe du portefeuille, P&L réalisé et latent, frais et transactions.

Les données sont lues dans `market_data/kraken/XBTEUR_5m.parquet` par défaut.
Cocher « Actualiser les bougies depuis Kraken » pour appeler l'API publique et
fusionner les dernières bougies clôturées. Aucune clé API n'est nécessaire.
[L’API OHLC Kraken](https://docs.kraken.com/api-reference/market-data/get-ohlc-data) ne fournit que les 720 dernières entrées : l'actualisation ne comble pas
les anciens trous à lui seul. Pour les compléter, cocher aussi
« Compléter les trous historiques (plus lent) » avant de lancer les backtests.
Les échanges historiques [Kraken Trades](https://docs.kraken.com/api-reference/market-data/get-recent-trades)
sont paginés, puis agrégés en bougies réelles.
L'opération peut prendre plusieurs minutes ; une réparation terminée est
sauvegardée après chaque trou. Aucun prix n'est interpolé et les périodes sans
échange restent absentes. Pour réparer en ligne de commande :

```sh
.venv/bin/python -m data.history --refresh
```

La réparation complète seulement les trous entre les bougies présentes ; elle
n'étend pas l'historique avant sa première bougie. Les téléchargements Kraken
qui n'aboutissent pas ne remplacent pas le fichier du trou en cours.
Le dashboard affiche la période disponible et les trous
éventuels, sans interpoler les bougies. Des signaux et des variations peuvent
manquer : compléter les données avant de conclure sur une stratégie.

Les derniers résultats et leurs paramètres sont conservés dans
`backtest_results.json`. Ils sont disponibles après redémarrage du serveur.
Une seule instance du dashboard doit utiliser ce fichier.

## Stratégies

- `strategies/moving_average.py` : moyennes simples 10/30.
- `strategies/momentum.py` : variation sur 12 bougies.
- `strategies/rsi.py` : RSI simple sur 14 variations, seuils 30/70.
- `strategies/trend_breakout.py` : breakout horaire avec filtre de tendance.
- `strategies/trend_pullback.py` : reprise au-dessus d'une moyenne 20 h après repli,
  avec moyenne 50 h montante et sortie sous la tendance ou les plus bas de 6 h.
- `strategies/volume_breakout.py` : cassure du canal de 20 bougies 30 min, confirmée
  par un volume supérieur à 1,5 fois la moyenne et un prix au-dessus de la moyenne 50.
- `strategies/bollinger_range.py` : réintégration de la bande basse 20/2 en 15 min,
  avec filtre de stabilité de la tendance et distance d'au moins 1 % à la moyenne.

Ces candidats ont des règles fixes, sans recherche des meilleurs paramètres sur
le fichier local. Tous utilisent uniquement des périodes entièrement clôturées
et exécutent les ordres à l'ouverture 5 min suivante dans le backtest. Les trous
internes interdisent les entrées et demandent une sortie. Les règles sont
contenues dans chaque fichier pour garder les versions indépendantes.
Les sorties sont des signaux de clôture : pas de stop exécuté en cours de bougie.
Le filtre de distance Bollinger n'est pas une garantie de gain ni une estimation
des frais si l'on modifie les hypothèses de simulation.

Références sur les indicateurs :
[Bollinger — Fidelity](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/bollinger-bands),
[Volume — Schwab](https://workplace.schwab.com/story/trading-volume-as-market-indicator).


### Candidats quantitatifs supplémentaires

Cinq hypothèses supplémentaires sont implémentées dans des fichiers indépendants :

- `volatility_momentum.py` : continuation à 24 h / 72 h, avec un seuil de mouvement
  de 1 % et une normalisation par la volatilité. L'hypothèse est la persistance
  des tendances ; le ratio de force n'est **pas** une statistique de significativité.
- `robust_trend.py` : pente médiane de Theil–Sen des log-clôtures sur 48 h. Le seuil
  d'entrée correspond à une pente de 1,5 % par jour, pas à une prévision de gain.
  La médiane réduit l'influence d'un pic isolé, sans garantir une meilleure performance.
- `squeeze_breakout.py` : sur 30 min, compression Bollinger / ATR puis cassure du
  canal antérieur confirmée par le volume. Une compression ne prédit pas la
  direction ; la cassure clôturée sert de confirmation. EMA20 amorcée sur 80 périodes.
- `volume_mean_reversion.py` : sur 1 h, retour vers une moyenne de clôtures pondérée
  par les volumes, après réintégration d'une déviation négative. Un filtre
  d'efficacité directionnelle évite les tendances les plus nettes ; il ne prouve
  pas la stationnarité. La référence exclut le prix et le volume courants.
- `session_breakout.py` : sur 30 min, range fixe 00–02 h UTC, entrée avant 12 h,
  signal de sortie dès 18 h. La session est une convention de recherche sur un
  marché 24/7, **pas** une ouverture officielle. Un arrêt du programme peut retarder
  l'exécution du signal horaire. VWMA des clôtures, pas VWAP des transactions.

Les seuils sont définis avant l'évaluation ; aucun balayage des paramètres n'a
été utilisé pour chercher un gagnant dans l'historique local. Les contrôles
comprennent les frais et minimums Kraken, la validation chronologique habituelle,
une simulation avec frais et slippage doublés et trois sous-périodes successives
à portefeuilles indépendants (avec amorce des indicateurs sur le passé disponible).
Ces vérifications ne transforment pas les données déjà consultées en véritable
échantillon inédit. Un résultat nul sans trade n'est pas une preuve de qualité.
Les données locales courtes ne suffisent pas à valider une stratégie à faible fréquence.

Toutes les sorties sont évaluées à la clôture et exécutées sur la bougie suivante :
il n'y a pas de stop intrabougie ni de dimensionnement dynamique selon le risque.
Les candidats restent soumis au filtre de validation ; le mode manuel paper est
une décision distincte. Aucun portefeuille existant n'est modifié par leur ajout.

Références : [canaux ATR — Schwab](https://toslc.thinkorswim.com/center/reference/Tech-Indicators/studies-library/G-L/KeltnerChannels),
[momentum des cryptomonnaies — article de recherche](https://www.sciencedirect.com/science/article/pii/S1062940821000590).
Ces références motivent les familles d'indicateurs ; elles ne valident pas nos règles ni nos paramètres.

### Breakout horaire avec filtre de tendance

Les bougies 5 min sont regroupées en heures complètes, sans utiliser les données
futures. Une décision est prise uniquement à la clôture horaire, puis exécutée à
l'ouverture de la bougie 5 min suivante par le même moteur.

- Entrée : clôture au-dessus du plus haut des **20 heures précédentes**, au-dessus
  d'une moyenne simple **72 h** orientée à la hausse.
- Sortie : clôture sous le plus bas des **10 heures précédentes** ou sous la moyenne
  72 h. Sinon, conservation de la position.
- Une heure incomplète dans les 73 dernières heures interdit l'entrée et demande
  la sortie d'une position existante. Pas de remplissage des trous.

Les canaux excluent l'heure courante. Les paramètres 20/10/72 sont fixes et n'ont
pas été optimisés sur le fichier local. Cette stratégie spot vise les tendances
haussières et peut rester en cash pendant longtemps. Il n'y a pas de stop exécuté
en cours d'heure : une variation intrahoraire n'est prise en compte qu'à la
clôture. Un petit nombre de trades ne permet pas de conclure à sa rentabilité.
Principe des canaux : [Donchian Channels — TradingView](https://www.tradingview.com/support/solutions/43000502253-donchian-channels-dc/).


Pour en ajouter une, créer un module dans `strategies/` avec :

```python
NAME = "Ma stratégie"
DESCRIPTION = "Règles d'entrée et de sortie"
WARMUP = 30


def strategy(history):
    # history contient uniquement les bougies connues à cet instant.
    return 0
```

`1` demande un achat, `-1` une vente, `0` conserve l'état courant.
Une position déjà ouverte empêche les achats supplémentaires ; une vente sans
position est ignorée. Les modules sont détectés automatiquement ; leur import
ne doit pas lancer de calcul ni faire d'appel réseau.

## Hypothèses du backtest

Signal sur la clôture de la bougie i, exécution à l'ouverture de i+1.
Achat à l'open augmenté du slippage, vente à l'open diminué du slippage.
La taille de position est une fraction du cash disponible, frais d'achat inclus.
Pas de levier ni de vente à découvert.
Les règles actuelles de la paire sont sauvegardées dans
`market_data/kraken/XBTEUR_rules.json` : quantité minimum, montant minimum hors
frais et nombre de décimales autorisées. Elles sont chargées une seule fois par
série de backtests. Les quantités sont arrondies vers le bas ; le reliquat reste
en cash. Un achat refusé ne modifie pas le portefeuille ; une vente refusée
conserve la position. Les tentatives et leurs motifs sont visibles dans la fiche
stratégie. Un signal persistant peut produire plusieurs tentatives sur des
bougies successives.

L'actualisation Kraken du dashboard ou `python -m backtest --refresh` actualise
également ce snapshot. Sans actualisation, le backtest reste utilisable hors
ligne. Ces règles actuelles sont appliquées à toute la période historique : les
changements de minimums passés ne sont pas reconstitués. Les prix simulés sont
des prix d'exécution au marché, pas des prix limites soumis au tick de cotation.
Documentation : [Kraken AssetPairs](https://docs.kraken.com/api-reference/market-data/get-tradable-asset-pairs).
 La position finale est valorisée au dernier
close, sans liquidation fictive ni frais de vente anticipés.

Le win rate porte uniquement sur les aller-retours clôturés après frais ; le
rendement et le drawdown portent sur tout le portefeuille, cash inclus. Le
benchmark buy & hold est brut et utilise tout le capital : son exposition est
donc différente de celle des stratégies. Les frais et le slippage par défaut
sont des hypothèses, pas des tarifs Kraken. Spread variable et liquidité ne sont pas simulés. Les stratégies fournies sont des exemples à tester.

## Ligne de commande

```sh
.venv/bin/python -m backtest
.venv/bin/python -m backtest --strategy rsi
.venv/bin/python -m backtest --refresh
```

`main.py` reste un point d'entrée compatible pour le backtest.

## Organisation

- `strategies/` : signaux des stratégies.
- `data/` : lecture locale et récupération Kraken.
- `backtest/` : exécution historique et métriques communes.
- `dashboard/` : serveur Python local et interface HTML sans dépendance JavaScript.
- `market_data/` : historique Parquet.
- `paper_trading/` : simulation multi-stratégies et sauvegardes séparées.

Le parcours est backtest, validation, puis paper. Le live n'est pas développé.
Le fichier historique `paper_state.json` est conservé sans migration ; les
nouveaux portefeuilles utilisent `paper_trading/states/`.

## Validation et passage en paper

Les seuils sont dans `validation_config.json` :

- Rendement net strictement supérieur à 0, sur le total et sur la validation.
- Drawdown maximum de 10 %, sur les deux périodes.
- Au moins 30 aller-retours clôturés au total et 10 en validation.
- Validation sur les 30 % les plus récents des bougies disponibles.

Le portefeuille de validation repart du capital initial sans position. Les
bougies antérieures restent accessibles pour amorcer les indicateurs mais aucun
ordre n'y est exécuté. Les paramètres ne sont pas optimisés. Cette découpe
chronologique ne garantit pas une indépendance statistique si les mêmes données
ont déjà servi à choisir la stratégie ; il faudra aussi tester d'autres périodes.

Chaque fiche « More » explique les critères atteints ou manquants. Les statuts
sont « À évaluer », « Données insuffisantes », « Refusée » et « Éligible au paper ».
Un nombre insuffisant de trades prime sur les autres échecs ; tous les critères
restent affichés. Un changement des seuils réévalue les résumés déjà calculés.
Un changement de la fraction de validation ou du code de la stratégie impose un
nouveau backtest. Ces seuils sont un filtre de recherche, pas une garantie de gain.

Dans la liste Backtests, **Démarrer paper** est actif uniquement pour une stratégie
éligible. Le serveur vérifie lui-même les critères pour le lancement normal. Le bouton **Lancer manuellement** permet de démarrer une stratégie même si ses
performances sont insuffisantes ou si elle manque de trades. Cette décision est
explicite, conservée dans sa sauvegarde et identifiée « Lancement manuel » dans
l'onglet Paper. Sa reprise conserve le mode manuel. Les seuils et le statut
normal ne sont pas modifiés. Un backtest terminé sur le code actuel reste requis ;
les erreurs de code et les règles Kraken ne sont pas contournées.

## Paper multi-stratégies

L'onglet **Paper** présente les portefeuilles démarrés : cash, BTC, P&L,
rendement, drawdown et trades. « More » ouvre une fiche dans un nouvel onglet.
Chaque stratégie a son propre capital fictif et son propre fichier JSON dans
`paper_trading/states/`, exclu de Git. La validation et le code approuvés sont
conservés avec le portefeuille.

Une seule boucle vérifie Kraken toutes les 15 secondes et partage les appels
OHLC/ticker entre les stratégies d'une même paire/intervalle. Chaque nouvelle
bougie clôturée est traitée une seule fois. La stratégie choisie est utilisée,
avec le budget, les frais, le slippage et les règles de quantité du backtest.
L'achat utilise l'ask courant, la vente le bid courant. Les données trop anciennes
ne déclenchent pas de simulation. Les positions et l'equity sont mises à jour à
chaque nouvelle bougie traitée, pas à chaque variation du ticker.

**Pause** n'effectue aucune vente. Les prix évoluent pendant la pause mais la
valorisation affichée reste celle du dernier traitement ; elle reprend à la
prochaine bougie traitée après reprise. Les signaux manqués ne sont pas rejoués.
Après redémarrage du serveur, les portefeuilles sont en pause ; utiliser
**Reprendre**. La reprise vérifie à nouveau l'éligibilité (sauf en mode manuel) et impose le même code
et les mêmes paramètres de portefeuille (un simple changement de date de capture
des règles n'est pas bloquant). Si le code change pendant le paper, il est mis
en pause. Pour l'instant, ne pas modifier une stratégie ayant un portefeuille
ouvert ; créer un nouveau fichier de stratégie pour une nouvelle version.

### Ajouter une stratégie pendant le paper

- Actualiser la page ou relancer des backtests ne coupe pas la boucle paper.
- Ajouter un **nouveau fichier** dans `strategies/` ne modifie pas les stratégies
  déjà actives. Le dashboard le découvre automatiquement ; lancer les backtests
  pour l'évaluer avant le paper. Aucune nouvelle stratégie n'est lancée d'office.
- Modifier le fichier d'une stratégie **déjà active** met cette stratégie en pause
  au prochain contrôle. Utiliser un nouveau nom de fichier pour une variante.
- Redémarrer le **serveur Python** remet les portefeuilles en pause ; leurs soldes
  et transactions sont conservés. Utiliser « Reprendre » après le redémarrage.

Le serveur doit rester actif pour que la simulation tourne. Lancer une seule
instance du dashboard pour un même projet. L'ancien `paper_state.json` n'est
ni repris automatiquement ni supprimé.

Alternative en ligne de commande, avec le même filtre et les mêmes sauvegardes :

```sh
.venv/bin/python -m paper_trading --strategy trend_breakout --once
```

Cette commande refuse de démarrer si la stratégie n'est pas éligible ; le lancement manuel se fait depuis le dashboard. Sans
`--once`, elle tourne jusqu'à Ctrl+C. Ne pas la lancer en parallèle du dashboard
sur les mêmes portefeuilles.

## Tests

Les cas limites NaN, infinis et valeurs invalides du drawdown sont laissés pour
plus tard, leurs tests sont conservés :

```sh
.venv/bin/python -m pytest -q -k 'not invalid_equity_raises_value_error'
```
