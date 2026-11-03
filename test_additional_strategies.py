import pandas as pd
import pytest

from strategies import get_strategies
from strategies.trend_pullback import strategy as pullback
from strategies.volume_breakout import strategy as breakout
from strategies.bollinger_range import strategy as bollinger


def bars(prices, minutes, volume_boost=False):
    n = minutes // 5
    close = [price for price in prices for _ in range(n)]
    volume = [1 / n] * len(close)
    if volume_boost:
        volume[-n:] = [2 / n] * n
    return pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=len(close), freq="5min", tz="UTC"),
        "open": close, "high": [p + .2 for p in close], "low": [p - .2 for p in close],
        "close": close, "volume": volume,
    })


def test_pullback_requires_reclaim_not_just_rising_prices():
    assert pullback(bars(list(range(100, 150)) + [130, 150], 60)) == 1
    assert pullback(bars(list(range(100, 152)), 60)) == 0
    assert pullback(bars(list(range(152, 100, -1)), 60)) == -1


def test_volume_breakout_requires_confirmation_and_excludes_current_high():
    data = bars(list(range(100, 150)), 30)
    assert breakout(data) == 0
    data = bars(list(range(100, 150)), 30, volume_boost=True)
    assert breakout(data) == 1
    data.loc[data.index[-1], "high"] = 10000
    assert breakout(data) == 1
    assert breakout(bars(list(range(150, 100, -1)), 30, volume_boost=True)) == -1


def test_bollinger_requires_reentry_and_room_for_costs():
    assert bollinger(bars([100] * 58 + [90, 95], 15)) == 1
    assert bollinger(bars([100] * 58 + [99.5, 99.75], 15)) == 0
    assert bollinger(bars([100] * 58 + [90, 85], 15)) == -1
    assert bollinger(bars([100] * 60, 15)) == -1


@pytest.mark.parametrize("name,minutes,periods", [
    ("trend_pullback", 60, 53), ("volume_breakout", 30, 51), ("bollinger_range", 15, 61),
])
def test_new_strategies_only_use_complete_periods(name, minutes, periods):
    fn = get_strategies()[name]["function"]
    data = bars(list(range(100, 100 + periods)), minutes, volume_boost=True)
    assert fn(data.iloc[:1]) == 0
    assert fn(data.iloc[:-1]) == 0  # Pas de décision avant la clôture de la période.
    incomplete = data.drop(index=2 * (minutes // 5))
    assert fn(incomplete) == -1  # Trou interne : pas d'entrée, sortie si déjà en position.
