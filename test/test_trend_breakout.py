import pandas as pd

from strategies.trend_breakout import strategy


def make_hourly_history(prices):
    # Une heure = 12 bougies de cinq minutes, pour tester le vrai chemin de calcul.
    close = [price for price in prices for _ in range(12)]
    return pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=len(close), freq="5min", tz="UTC"),
        "open": close, "high": [p + 0.2 for p in close],
        "low": [p - 0.2 for p in close], "close": close, "volume": 1,
    })


def test_breakout_in_rising_trend_enters():
    data = make_hourly_history(list(range(100, 174)))
    assert strategy(data) == 1
    # La bougie courante a un high supérieur au close : le canal doit l'exclure.
    data.loc[data.index[-1], "high"] = 10000
    assert strategy(data) == 1


def test_no_decision_during_incomplete_hour():
    data = make_hourly_history(list(range(100, 174)))
    assert strategy(data.iloc[:-1]) == 0


def test_flat_market_does_not_trade():
    assert strategy(make_hourly_history([100] * 74)) == 0


def test_breakout_without_rising_trend_does_not_enter():
    # Nouveau plus haut local, mais moyenne longue encore orientée à la baisse.
    prices = [200] * 52 + [100] * 21 + [180]
    assert strategy(make_hourly_history(prices)) == 0


def test_trend_loss_exits():
    prices = list(range(100, 173)) + [120]
    assert strategy(make_hourly_history(prices)) == -1


def test_gap_prevents_entry():
    data = make_hourly_history(list(range(100, 175)))
    data = data.drop(index=36)
    assert strategy(data) == -1


def test_warmup_does_not_trade():
    assert strategy(make_hourly_history([100] * 20)) == 0
