"""Hypothèse de session UTC sur marché 24/7, sans prétendre à une ouverture officielle."""
import pandas as pd

NAME = "Cassure de session UTC · 30 min"
DESCRIPTION = (
    "Range 00–02 h UTC complet, largeur entre 0,5 et 3 %. Achat entre 02 et 12 h UTC "
    "sur franchissement du range, volume > 1,5 fois sa moyenne et prix au-dessus de la VWMA de session. "
    "Sortie sous VWMA / bas du range, ou dès 18 h UTC. Pas d'ouverture officielle en crypto."
)
WARMUP = 5 * 6


def strategy(data):
    if len(data) < WARMUP:
        return 0
    last = pd.Timestamp(data.iloc[-1]["timestamp"])
    if (last.minute + 5) % 30:
        return 0
    cutoff = last + pd.Timedelta(minutes=5)
    if cutoff.hour < 2 or cutoff.hour >= 18:
        return -1
    day = cutoff.floor("D")
    recent = data.loc[data.timestamp >= day].set_index("timestamp")
    bars = recent.resample("30min", origin="epoch").agg(
        high=("high", "max"), low=("low", "min"), close=("close", "last"),
        volume=("volume", "sum"), candles=("close", "count"),
    )
    if bars.empty or bars.index[0] != day or (bars.candles != 6).any():
        return -1
    if len(bars) < 5:
        return 0
    opening = bars.iloc[:4]
    upper, lower = opening.high.max(), opening.low.min()
    if bars.volume.sum() <= 0:
        return -1
    vwma = (bars.close * bars.volume).sum() / bars.volume.sum()
    price, previous = bars.close.iloc[-1], bars.close.iloc[-2]
    if price < vwma or price < lower:
        return -1
    width = (upper - lower) / opening.close.iloc[-1]
    volume = opening.volume.mean()
    if (cutoff.hour < 12 and .005 <= width <= .03 and previous <= upper < price
            and price > vwma and volume > 0 and bars.volume.iloc[-1] > 1.5 * volume):
        return 1
    return 0
