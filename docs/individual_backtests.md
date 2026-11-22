# Backtests individuels — interface pour expériences

Un appel = une stratégie existante, une configuration, un résultat JSON natif.
Le moteur d'exécution reste `backtest.engine.backtest`, via `report.run_strategy`.
Aucun agent/framework, stratégie supplémentaire ou moteur live n'est ajouté.

## CLI

Depuis la racine, avec les dépendances du projet installées :

```sh
python -m backtest --strategy moving_average --params '{"short_window":5,"long_window":20}' --symbol XBTEUR --interval 5 --start 2026-10-01T00:00:00Z --end 2026-11-01T00:00:00Z --capital 100 --position-size 0.10 --fee-rate 0.0025 --slippage-rate 0.001 --json
```

Adapter la période aux données présentes. `interval` est en **minutes** ; frais,
slippage et taille sont des fractions, pas des pourcentages. Sans `--json`, un
résumé texte est affiché. Code de sortie `0` : completed ; `1` : erreur d'exécution
structurée. Les erreurs de syntaxe des arguments/JSON restent des erreurs argparse
sur stderr, code `2`. Une stratégie inconnue avec `--json` renvoie un résultat
structuré d'erreur, sans lancer d'autre stratégie.

Le défaut est **hors ligne** : Parquet local `market_data/kraken/<symbol>_<interval>m.parquet`
et snapshot `<symbol>_rules.json` obligatoires. Une absence de snapshot ne
provoque pas d'appel réseau silencieux. `--refresh` actualise explicitement
l'historique/règles via le market data existant avant le backtest ; cela peut
modifier les caches locaux. La récupération Kraken ne garantit pas qu'une
période ancienne complète sera disponible.

Le batch existant conserve ses commandes et son résumé texte :

```sh
python -m backtest
python -m backtest --refresh
```

Les options d'expérience exigent `--strategy` ; elles ne sont pas appliquées
silencieusement à un batch. Le dashboard et sa validation ne sont pas modifiés.

## API Python

```python
from backtest.experiment import run_experiment

experiments = [
    run_experiment(
        "moving_average",
        parameters={"short_window": short, "long_window": long},
        symbol="XBTEUR", interval=5,
        start="2026-10-01T00:00:00Z", end="2026-11-01T00:00:00Z",
        config={"capital": 100, "position_size": 0.1,
                "fee_rate": 0.0025, "slippage_rate": 0.001},
    )
    for short, long in [(5, 20), (10, 30)]
]
# Examiner status avant metrics, puis comparer à dataset/config identiques.
```

Pour des tests ou une recherche entièrement en mémoire : fournir `data=<DataFrame>`
et `market_rules={"ordermin": "...", "costmin": "...", "lot_decimals": 8}`.
Ces deux arguments évitent le chargement de leurs caches respectifs. La paire
associée aux données injectées reste la responsabilité de l'appelant ; un
snapshot portant explicitement un autre `symbol` est refusé.

L'appel ne sauvegarde pas de résultat global, ne lance aucun serveur et ne
modifie ni DataFrame entrant, ni dictionnaire de paramètres, ni variables
partagées des stratégies. L'appelant conserve ses résultats pour comparaison.

## Paramètres exposés

Les valeurs par défaut et signaux historiques sont conservés :

- `moving_average` : `short_window=10`, `long_window=30` ; entiers,
  `0 < short_window < long_window`. Amorce = long_window.
- `momentum` : `lookback=12` entier positif ; amorce = lookback + 1.
- `rsi` : `period=14` entier positif, `oversold=30`, `overbought=70` avec
  `0 <= oversold < overbought <= 100` ; amorce = period + 1.

Les autres stratégies existantes restent sélectionnables avec leurs paramètres
fixes ; elles refusent les overrides inconnus et exigent des bougies de **5 min**,
car leurs agrégations/cadences supposent ce pas. Les trois stratégies ci-dessus
acceptent les autres intervalles Kraken listés par la CLI/API, si un historique
correspondant existe. Leurs fenêtres représentent des nombres de bougies :
changer le timeframe change donc la durée économique des indicateurs.

L'exposition d'autres paramètres viendra explicitement plus tard ; pas de
modification de constantes globales, de copie dynamique de module ou d'optimiseur.
Les hashes de source des trois modules changent : les validations paper liées
à l'ancienne version doivent être refaites suivant le mécanisme existant.
Aucun portefeuille ni état paper n'est migré ou démarré par cette fonctionnalité.

## Contrat de résultat, schema_version=1

- `status` : `completed` ou `error` ; `error` vaut null en succès, sinon contient
  `type` et un message contrôlé. `metrics` et `details` sont null en échec.
- `strategy` : nom du module, paramètres effectifs (défauts inclus), source_hash.
- `dataset` : paire, interval_minutes, rows, source locale/injectée, SHA-256 des
  lignes OHLCV sélectionnées normalisées (UTC et valeurs numériques), période
  demandée et effective, warmup_candles. Les dates sont des chaînes ISO UTC.
- `config` : capital, taille, frais, slippage, paire, intervalle et règles
  effectivement utilisées (snapshot inclus pour les comparaisons).
- `metrics` : métriques existantes du rapport, plus `sharpe_ratio` et `trade_count`.
  `pnl` est l'equity finale moins le capital initial, net des frais déjà engagés ;
  `return` et `max_drawdown` sont des fractions. `trade_count` compte les opérations
  exécutées achat + vente ; `closed_trades` compte les ventes/cycles clôturés.
  `win_rate` est calculé sur les cycles clôturés, frais inclus ; zéro sans clôture.
  `rejected_orders` indique les tentatives refusées par les minimums de marché.
- `details` : trades, equity_curve et rejected_orders, tous sérialisables en JSON
  strict sans Timestamp/scalaire NumPy, NaN ou Infinity.

Une erreur de chargement peut survenir avant que dataset/config/paramètres
soient disponibles ; ces champs restent alors null ou vides. Les erreurs
internes de stratégie n'exposent pas leur message brut. Aucun résultat partiel
n'est présenté comme un succès.

Le **Sharpe annualisé** utilise les rendements simples consécutifs de l'equity,
avec taux sans risque nul, écart-type échantillonnal (`ddof=1`), facteur
`sqrt(365 * 24 * 60 / interval_minutes)` pour le marché crypto 24/7. Il vaut
null si moins de deux rendements ou variance nulle. Il inclut l'amorce et les
périodes FLAT comme le reste de la courbe ; il n'est pas une preuve de rentabilité.

## Période et hypothèses conservées

Dates naïves interprétées en UTC. Bornes `[start,end)` : seules les bougies dont
l'ouverture est >= start et la clôture <= end sont retenues. La bougie encore
ouverte à l'instant du test est retirée. `effective_end` est la clôture de la
dernière bougie réellement utilisée. Une borne au-delà des données disponibles
ne crée pas de données fictives : examiner la période effective dans le résultat.

L'amorce des indicateurs est **dans la période choisie**, sans lecture de données
antérieures supplémentaires. Il faut au moins warmup + 1 bougies ; aucune opération
n'est possible avant l'amorce propre à la stratégie. Un signal ne voit que son
préfixe de clôtures et est exécuté sur l'ouverture suivante, avec les frais,
slippage et taille de position du moteur. Pas de short, levier, pyramiding ou
liquidation finale : la dernière position reste valorisée au close final et
les frais d'une vente hypothétique ne sont pas ajoutés.

L'interface individuelle refuse trous, timestamps dupliqués/désordonnés, valeurs
non finies, volumes négatifs et OHLC incohérents dans les données sélectionnées.
Le chargeur local existant normalise/trie/déduplique déjà le Parquet ; ces
normalisations précèdent les contrôles de l'interface. Le batch historique n'est
pas durci par cette mission et peut donc avoir un comportement différent sur
une entrée invalide ou discontinue.

## Limites pour de futurs agents

- Pas de train/test automatique, contrôle d'overfitting, test statistique de Sharpe,
  optimisation ou sélection automatique paper/live. Comparer les expériences à
  période, données, frais et capital identiques, en gardant un échantillon séparé.
- Exécutions déterministes à l'open suivant avec slippage fixe ; aucun carnet,
  remplissage partiel, latence ou modèle de liquidité.
- Les règles Kraken sont un snapshot actuel, pas un historique des contraintes
  de chaque date. Les paramètres économiques et dépendances Python peuvent
  affecter les comparaisons ; le source_hash du module ne couvre pas tout le dépôt.
- Les dix cas limites déjà défaillants de `test_metrics` restent inchangés. Le
  wrapper refuse les equity non finies/non positives avant ses métriques ; cela
  ne corrige pas les fonctions historiques lorsqu'elles sont appelées directement.
- Pas de registre persistant d'expériences, cache de résultats ou moteur d'agents.
  La sortie JSON reste volontairement simple ; la courbe complète peut être grosse.

Tests ciblés : `python -m pytest test/test_experiment.py -q` ; suite complète :
`python -m pytest -q`.

## Validation de cette livraison

- 43 nouveaux tests ciblés réussis.
- Suite complète sans exclusion : 351 réussis, les mêmes 10 échecs préexistants
  de `test_metrics` (equity négatives intermédiaires, NaN et infinis).
- Comparaison des résultats individuels aux rapports batch, vérification des
  anciennes formules de signaux par défaut, paramètres isolés, période/timeframe,
  ouverture suivante, frais, position non liquidée, Sharpe et erreurs JSON.
- Démonstration API exécutée sur données synthétiques en mémoire : résultat
  completed et JSON strict. Aucun appel Kraken ni credentials utilisés.

L'environnement `.venv` synchronisé était bloqué sur des fichiers déchargés par
macOS/iCloud. Les vérifications ont été exécutées dans un venv temporaire hors
du dépôt, avec les mêmes versions des dépendances principales que le projet :
pandas 3.0.6, NumPy 2.5.4, pytest 9.1.1, pyarrow 26.0.0, requests 2.34.2.
Le `.venv` du projet et `requirements.txt` n'ont pas été modifiés.
