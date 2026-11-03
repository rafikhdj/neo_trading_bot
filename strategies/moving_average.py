NAME = "Moyennes mobiles"
DESCRIPTION = "Long si la moyenne des 10 dernières clôtures dépasse celle des 30 ; sinon sortie."
WARMUP = 30


def strategy(data):
    if len(data) < WARMUP:
        return 0
    close = data["close"]
    return 1 if close.iloc[-10:].mean() > close.iloc[-30:].mean() else -1
