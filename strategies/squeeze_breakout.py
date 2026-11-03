"""Expansion après compression : bandes de Bollinger à l'intérieur du canal ATR."""
import pandas as pd

NAME = "Compression / expansion · 30 min"
DESCRIPTION = (
    "Compression précédente : largeur Bollinger 20/2 < 3 ATR20. Achat sur cassure "
    "du plus haut des 20 périodes antérieures, au-dessus de la moyenne 50, "
    "avec volume supérieur à leur moyenne. Sortie sous EMA20 ou plus bas des 6 périodes."
)
WARMUP = 80 * 6


def strategy(data):
    if len(data) < WARMUP or (pd.Timestamp(data.iloc[-1]["timestamp"]).minute + 5) % 30:
        return 0
    bars = data.iloc[-81 * 6:].set_index("timestamp").resample("30min", origin="epoch").agg(
        high=("high", "max"), low=("low", "min"), close=("close", "last"),
        volume=("volume", "sum"), candles=("close", "count"),
    ).iloc[-80:]
    if len(bars) < 80:
        return 0
    if (bars.candles != 6).any():
        return -1
    prior = bars.iloc[:-1]
    price = bars.close.iloc[-1]
    center = bars.close.ewm(span=20, adjust=False).mean().iloc[-1]
    if price < center or price < prior.low.iloc[-6:].min():
        return -1
    true_range = pd.concat([
        prior.high - prior.low, (prior.high - prior.close.shift()).abs(),
        (prior.low - prior.close.shift()).abs(),
    ], axis=1).max(axis=1)
    atr = true_range.iloc[-20:].mean()
    compressed = 4 * prior.close.iloc[-20:].std(ddof=0) < 3 * atr
    average_volume = prior.volume.iloc[-20:].mean()
    if (atr > 0 and compressed and price > prior.high.iloc[-20:].max()
            and price > bars.close.iloc[-50:].mean() and average_volume > 0
            and bars.volume.iloc[-1] > average_volume):
        return 1
    return 0
