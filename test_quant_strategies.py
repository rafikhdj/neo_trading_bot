import numpy as np
import pytest

from strategies import get_strategies
from strategies.volatility_momentum import strategy as momentum
from strategies.robust_trend import strategy as robust
from strategies.squeeze_breakout import strategy as squeeze
from strategies.volume_mean_reversion import strategy as reversion
from strategies.session_breakout import strategy as session
from test_additional_strategies import bars


def test_normalized_momentum_needs_both_horizons_and_signal_over_noise():
    trend = 100 * np.exp(.002 * np.arange(74))
    assert momentum(bars(trend, 60)) == 1
    assert momentum(bars(trend[::-1], 60)) == -1
    assert momentum(bars(100 * np.exp(.0001 * np.arange(74)), 60)) == 0
    assert momentum(bars([150] * 49 + list(np.linspace(90, 100, 25)), 60)) == 0
    noisy = [100] * 50 + list(100 * np.exp(np.cumsum(np.tile([.04, -.039], 12))))
    assert momentum(bars(noisy, 60)) == 0


def test_robust_slope_does_not_mistake_one_spike_for_a_trend():
    assert robust(bars([100] * 47 + [160], 60)) != 1
    trend = 100 * np.exp(.002 * np.arange(48))
    assert robust(bars(trend, 60)) == 1
    assert robust(bars(trend[::-1], 60)) == -1
    assert robust(bars(100 * np.exp(.0001 * np.arange(48)), 60)) == 0


def test_squeeze_requires_prior_compression_volume_and_breakout():
    data = bars([100] * 79 + [102], 30, volume_boost=True)
    assert squeeze(data) == 1
    data.loc[data.index[-1], "high"] = 10000  # Canal calculé sans le high courant.
    assert squeeze(data) == 1
    assert squeeze(bars([100] * 79 + [102], 30)) == 0
    assert squeeze(bars(list(range(100, 179)) + [185], 30, volume_boost=True)) == 0
    assert squeeze(bars([100] * 79 + [98], 30, volume_boost=True)) == -1


def test_weighted_reversion_uses_prior_volume_and_waits_for_reentry():
    data = bars([98, 102] * 23 + [90, 95], 60)
    assert reversion(data) == 1
    # Le volume de l'heure courante ne doit pas déplacer sa propre référence.
    data.loc[data.index[-12:], "volume"] = 10000
    assert reversion(data) == 1
    # En revanche, le volume antérieur modifie bien la référence pondérée.
    data.loc[data.index[-24:-12], "volume"] = 100000
    assert reversion(data) == -1
    assert reversion(bars(list(range(148, 100, -1)), 60)) != 1
    empty_volume = bars([98, 102] * 23 + [90, 95], 60)
    empty_volume["volume"] = 0
    assert reversion(empty_volume) == -1


def session_history(boost=True):
    data = bars([100] * 4 + [102], 30, volume_boost=boost)
    data.loc[data.index[:24], "high"] = 101
    data.loc[data.index[:24], "low"] = 99
    return data


def test_session_range_is_fixed_before_breakout_and_requires_volume():
    data = session_history()
    assert session(data) == 1
    assert session(session_history(False)) == 0
    assert session(bars([100] * 37, 30)) == -1  # Sortie horaire à partir de 18 h UTC.
    assert session(data.iloc[:-1]) == 0
    data.loc[data.index[:24], "high"] = 110  # Range excessif : pas d'entrée.
    assert session(data) != 1


@pytest.mark.parametrize("name,minutes,count", [
    ("volatility_momentum", 60, 75), ("robust_trend", 60, 49),
    ("squeeze_breakout", 30, 81), ("volume_mean_reversion", 60, 49),
    ("session_breakout", 30, 6),
])
def test_quant_strategies_reject_incomplete_history(name, minutes, count):
    fn = get_strategies()[name]["function"]
    data = bars(list(range(100, 100 + count)), minutes, volume_boost=True)
    assert fn(data.iloc[:1]) == 0
    assert fn(data.iloc[:-1]) == 0
    assert fn(data.drop(index=2 * (minutes // 5))) == -1


@pytest.mark.parametrize("name", ["volatility_momentum", "robust_trend", "squeeze_breakout", "volume_mean_reversion", "session_breakout"])
def test_signals_are_invariant_to_price_and_volume_units(name):
    examples = {
        "volatility_momentum": bars(100 * np.exp(.002 * np.arange(74)), 60),
        "robust_trend": bars(100 * np.exp(.002 * np.arange(48)), 60),
        "squeeze_breakout": bars([100] * 79 + [102], 30, volume_boost=True),
        "volume_mean_reversion": bars([98, 102] * 23 + [90, 95], 60),
        "session_breakout": session_history(),
    }
    data = examples[name]
    fn = get_strategies()[name]["function"]
    assert fn(data) == 1
    converted = data.copy()
    converted[["open", "high", "low", "close"]] *= 1000
    converted["volume"] *= 10
    assert fn(converted) == 1
