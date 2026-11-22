NAME = "Moyennes mobiles"
DESCRIPTION = "Long si la moyenne des 10 dernières clôtures dépasse celle des 30 ; sinon sortie."
WARMUP = 30


def strategy(data, short_window=10, long_window=30):
    if not (isinstance(short_window, int) and not isinstance(short_window, bool)
            and isinstance(long_window, int) and not isinstance(long_window, bool)
            and 0 < short_window < long_window):
        raise ValueError("Fenêtres entières : 0 < short_window < long_window.")
    if len(data) < long_window:
        return 0
    close = data["close"]
    return 1 if close.iloc[-short_window:].mean() > close.iloc[-long_window:].mean() else -1
