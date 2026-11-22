NAME = "Momentum"
DESCRIPTION = "Long si le prix a progressé sur 12 bougies ; sortie si la variation est négative ou nulle."
WARMUP = 13


def strategy(data, lookback=12):
    if not isinstance(lookback, int) or isinstance(lookback, bool) or lookback < 1:
        raise ValueError("lookback doit être un entier positif.")
    if len(data) < lookback + 1:
        return 0
    return 1 if data["close"].iloc[-1] > data["close"].iloc[-lookback - 1] else -1
