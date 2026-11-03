"""Cassure d'un canal confirmée par le volume, sans indicateur futur."""
import pandas as pd

NAME = "Breakout volume · 30 min"
DESCRIPTION = (
    "Clôtures 30 min complètes : achat au-dessus du plus haut des 20 périodes précédentes, "
    "avec volume > 1,5 fois leur moyenne et prix au-dessus de la moyenne 50 périodes. "
    "Sortie sous la moyenne 20 périodes ou sous le plus bas des 6 périodes précédentes."
)
WARMUP = 50 * 6


def strategy(data):
    if len(data) < WARMUP or (pd.Timestamp(data.iloc[-1]["timestamp"]).minute + 5) % 30:
        return 0
    bars = data.iloc[-51 * 6:].set_index("timestamp").resample("30min", origin="epoch").agg(
        high=("high", "max"), low=("low", "min"), close=("close", "last"),
        volume=("volume", "sum"), candles=("close", "count"),
    ).iloc[-50:]
    if len(bars) < 50:
        return 0
    if (bars.candles != 6).any():
        return -1
    prior = bars.iloc[:-1]
    close = bars.close.iloc[-1]
    if close < bars.close.iloc[-20:].mean() or close < prior.low.iloc[-6:].min():
        return -1
    average_volume = prior.volume.iloc[-20:].mean()
    if (close > prior.high.iloc[-20:].max() and close > bars.close.mean()
            and average_volume > 0 and bars.volume.iloc[-1] > 1.5 * average_volume):
        return 1
    return 0
