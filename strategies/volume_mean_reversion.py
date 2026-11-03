"""Réintégration d'une déviation à la moyenne pondérée, hors tendance efficace."""
import numpy as np
import pandas as pd

NAME = "Réversion VWMA · 1 h"
DESCRIPTION = (
    "Moyenne et dispersion des 24 clôtures précédentes pondérées par le volume. "
    "Achat après retour de z < -2 vers -2 < z < -1, si efficacité directionnelle 24 h < 0,25 "
    "et distance à la moyenne >= 1 %. Sortie à la moyenne ou si z < -3. VWMA de clôtures, pas VWAP des échanges."
)
WARMUP = 48 * 12


def strategy(data):
    if len(data) < WARMUP or pd.Timestamp(data.iloc[-1]["timestamp"]).minute != 55:
        return 0
    bars = data.iloc[-49 * 12:].set_index("timestamp").resample("1h", origin="epoch").agg(
        close=("close", "last"), volume=("volume", "sum"), candles=("close", "count"),
    ).iloc[-48:]
    if len(bars) < 48:
        return 0
    if (bars.candles != 12).any():
        return -1

    references = []
    for window in (bars.iloc[-25:-1], bars.iloc[-26:-2]):
        weights = window.volume.to_numpy()
        if weights.sum() <= 0:
            return -1
        values = window.close.to_numpy()
        mean = float(np.average(values, weights=weights))
        sigma = float(np.sqrt(np.average((values - mean) ** 2, weights=weights)))
        references.append((mean, sigma))
    (mean, sigma), (previous_mean, previous_sigma) = references
    price = bars.close.iloc[-1]
    if sigma <= 0 or previous_sigma <= 0:
        return -1
    z = (price - mean) / sigma
    if price >= mean or z < -3:
        return -1
    path = bars.close.iloc[-25:].to_numpy()
    distance = float(np.abs(np.diff(path)).sum())
    efficiency = abs(path[-1] - path[0]) / distance if distance > 0 else 0
    previous_z = (bars.close.iloc[-2] - previous_mean) / previous_sigma
    if efficiency < .25 and previous_z < -2 and -2 < z < -1 and mean / price - 1 >= .01:
        return 1
    return 0
