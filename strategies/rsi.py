NAME = "RSI"
DESCRIPTION = "RSI simple sur 14 variations : achat sous 30, sortie au-dessus de 70 ; conservation entre les deux."
WARMUP = 15


def strategy(data):
    if len(data) < WARMUP:
        return 0
    changes = data["close"].iloc[-15:].diff().dropna()
    gain = changes.clip(lower=0).mean()
    loss = -changes.clip(upper=0).mean()
    if loss == 0:
        rsi = 100 if gain > 0 else 50
    else:
        rsi = 100 - 100 / (1 + gain / loss)
    if rsi < 30:
        return 1
    if rsi > 70:
        return -1
    return 0
