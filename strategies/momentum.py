NAME = "Momentum"
DESCRIPTION = "Long si le prix a progressé sur 12 bougies ; sortie si la variation est négative ou nulle."
WARMUP = 13


def strategy(data):
    if len(data) < WARMUP:
        return 0
    return 1 if data["close"].iloc[-1] > data["close"].iloc[-13] else -1
