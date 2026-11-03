"""Pente médiane de Theil–Sen sur les log-prix : moins sensible à un pic isolé."""
import numpy as np
import pandas as pd

NAME = "Tendance robuste · 1 h"
DESCRIPTION = (
    "Clôtures horaires : pente médiane des paires de log-prix sur 48 h. "
    "Achat si pente équivalente > 1,5 % par jour et prix au-dessus de sa moyenne 48 h. "
    "Sortie si pente <= 0 ou prix sous sa moyenne 24 h. La pente n'est pas une prévision."
)
WARMUP = 48 * 12


def strategy(data):
    if len(data) < WARMUP or pd.Timestamp(data.iloc[-1]["timestamp"]).minute != 55:
        return 0
    bars = data.iloc[-49 * 12:].set_index("timestamp").resample("1h", origin="epoch").agg(
        close=("close", "last"), candles=("close", "count"),
    ).iloc[-48:]
    if len(bars) < 48:
        return 0
    if (bars.candles != 12).any():
        return -1
    close = bars.close.to_numpy()
    logs = np.log(close)
    left, right = np.triu_indices(len(logs), k=1)
    slope = float(np.median((logs[right] - logs[left]) / (right - left)))
    if slope <= 0 or close[-1] < close[-24:].mean():
        return -1
    if slope > np.log1p(.015) / 24 and close[-1] > close.mean():
        return 1
    return 0
