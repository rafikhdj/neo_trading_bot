# Neo · Bot Kraken

Un fichier par stratégie, un moteur de backtest commun et un dashboard local.
Cette étape concerne uniquement les backtests ; le paper et le live ne sont pas
intégrés au dashboard.

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
- P&L net, rendement du portefeuille, drawdown maximum, win rate et trades clôturés.
- Bouton « More » : courbe du portefeuille, P&L réalisé et latent, frais et transactions.

Les données sont lues dans `market_data/kraken/XBTEUR_5m.parquet` par défaut.
Cocher « Actualiser les bougies depuis Kraken » pour appeler l'API publique et
fusionner les dernières bougies clôturées. Aucune clé API n'est nécessaire.
L'API OHLC ne fournit que les 720 dernières entrées : l'actualisation ne comble pas
les anciens trous. Le dashboard affiche la période disponible et les trous
éventuels, sans interpoler les bougies. Des signaux et des variations peuvent
manquer : compléter les données avant de conclure sur une stratégie.

Les derniers résultats et leurs paramètres sont conservés dans
`backtest_results.json`. Ils sont disponibles après redémarrage du serveur.
Une seule instance du dashboard doit utiliser ce fichier.

## Stratégies

- `strategies/moving_average.py` : moyennes simples 10/30.
- `strategies/momentum.py` : variation sur 12 bougies.
- `strategies/rsi.py` : RSI simple sur 14 variations, seuils 30/70.

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
Pas de levier ni de vente à découvert. La position finale est valorisée au dernier
close, sans liquidation fictive ni frais de vente anticipés.

Le win rate porte uniquement sur les aller-retours clôturés après frais ; le
rendement et le drawdown portent sur tout le portefeuille, cash inclus. Le
benchmark buy & hold est brut et utilise tout le capital : son exposition est
donc différente de celle des stratégies. Les frais et le slippage par défaut
sont des hypothèses, pas des tarifs Kraken. Minimums d'ordre, spread variable et
liquidité ne sont pas simulés. Les stratégies fournies sont des exemples à tester.

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
- `paper_trading/` : première boucle de simulation, conservée pour une étape ultérieure.

Le parcours prévu est backtest, puis paper, puis live. Pour le moment, aucun
lancement paper/live ni promotion automatique n'est proposé dans le dashboard.
Le fichier `paper_state.json` existant n'est pas modifié.

## Tests

Les cas limites NaN, infinis et valeurs invalides du drawdown sont laissés pour
plus tard, leurs tests sont conservés :

```sh
.venv/bin/python -m pytest -q -k 'not invalid_equity_raises_value_error'
```
