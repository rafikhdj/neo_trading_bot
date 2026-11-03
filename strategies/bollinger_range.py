"""Retour à la moyenne après réintégration des bandes, avec filtre de range."""
import pandas as pd

NAME = "Bollinger range · 15 min"
DESCRIPTION = (
    "Clôtures 15 min : achat à la réintégration de la bande basse (20 périodes, 2 écarts-types), "
    "si la moyenne 48 périodes varie de moins de 0,5 % sur 8 périodes et si le retour "
    "à la moyenne offre au moins 1 %. Sortie à la moyenne ou sur nouvelle rupture de la bande basse."
)
WARMUP = 60 * 3


def strategy(data):
    if len(data) < WARMUP or (pd.Timestamp(data.iloc[-1]["timestamp"]).minute + 5) % 15:
        return 0
    bars = data.iloc[-61 * 3:].set_index("timestamp").resample("15min", origin="epoch").agg(
        close=("close", "last"), candles=("close", "count"),
    ).iloc[-60:]
    if len(bars) < 60:
        return 0
    if (bars.candles != 3).any():
        return -1
    close = bars.close
    middle = close.iloc[-20:].mean()
    lower = middle - 2 * close.iloc[-20:].std(ddof=0)
    previous_middle = close.iloc[-21:-1].mean()
    previous_lower = previous_middle - 2 * close.iloc[-21:-1].std(ddof=0)
    price = close.iloc[-1]
    if price >= middle or price < lower:
        return -1
    trend_now = close.iloc[-48:].mean()
    trend_before = close.iloc[-56:-8].mean()
    in_range = abs(trend_now / trend_before - 1) < 0.005
    room = middle / price - 1
    if in_range and room >= 0.01 and close.iloc[-2] < previous_lower and price > lower:
        return 1
    return 0
