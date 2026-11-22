import math

NAME = "RSI"
DESCRIPTION = "RSI simple sur 14 variations : achat sous 30, sortie au-dessus de 70 ; conservation entre les deux."
WARMUP = 15


def strategy(data, period=14, oversold=30.0, overbought=70.0):
    if not isinstance(period, int) or isinstance(period, bool) or period < 1:
        raise ValueError("period doit être un entier positif.")
    if not (math.isfinite(oversold) and math.isfinite(overbought)
            and 0 <= oversold < overbought <= 100):
        raise ValueError("Seuils RSI : 0 <= oversold < overbought <= 100.")
    if len(data) < period + 1:
        return 0
    changes = data["close"].iloc[-period - 1:].diff().dropna()
    gain = changes.clip(lower=0).mean()
    loss = -changes.clip(upper=0).mean()
    if loss == 0:
        rsi = 100 if gain > 0 else 50
    else:
        rsi = 100 - 100 / (1 + gain / loss)
    if rsi < oversold:
        return 1
    if rsi > overbought:
        return -1
    return 0
