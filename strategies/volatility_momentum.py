"""Momentum multi-horizon avec seuil de mouvement et normalisation du bruit."""
import numpy as np
import pandas as pd

NAME = "Momentum normalisé · 1 h"
DESCRIPTION = (
    "Clôtures horaires complètes : achat si rendements 24 h et 72 h positifs, "
    "mouvement 24 h > 1 % et somme des log-rendements / (écart-type × racine de 24) > 1. "
    "Sortie si rendement 24 h <= 0. Ce ratio est descriptif, pas un test de significativité."
)
WARMUP = 74 * 12


def strategy(data):
    if len(data) < WARMUP or pd.Timestamp(data.iloc[-1]["timestamp"]).minute != 55:
        return 0
    bars = data.iloc[-75 * 12:].set_index("timestamp").resample("1h", origin="epoch").agg(
        close=("close", "last"), candles=("close", "count"),
    ).iloc[-74:]
    if len(bars) < 74:
        return 0
    if (bars.candles != 12).any():
        return -1
    close = bars.close
    r24 = close.iloc[-1] / close.iloc[-25] - 1
    r72 = close.iloc[-1] / close.iloc[-73] - 1
    if r24 <= 0:
        return -1
    returns = np.diff(np.log(close.iloc[-25:].to_numpy()))
    noise = float(returns.std(ddof=1))
    # Plancher pour ne pas transformer une série quasi constante en ratio infini.
    strength = float(returns.sum()) / (max(noise, 1e-6) * np.sqrt(24))
    if r24 > .01 and r72 > 0 and strength > 1:
        return 1
    return 0
