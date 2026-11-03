import pandas as pd
import pytest

from strategies import get_strategies
from strategies.rsi import strategy as rsi


@pytest.mark.parametrize("name", ["moving_average", "momentum", "rsi"])
def test_strategy_warmup_and_directions(name):
    spec = get_strategies()[name]
    assert spec["function"](pd.DataFrame({"close": [100]})) == 0
    up = pd.DataFrame({"close": list(range(100, 140))})
    down = pd.DataFrame({"close": list(range(140, 100, -1))})
    expected = (-1, 1) if name == "rsi" else (1, -1)
    assert spec["function"](up) == expected[0]
    assert spec["function"](down) == expected[1]


def test_flat_rsi_holds():
    assert rsi(pd.DataFrame({"close": [100] * 30})) == 0


def test_invalid_module_is_reported_without_breaking_discovery(monkeypatch):
    def invalid(*args):
        raise SyntaxError("Syntaxe invalide")
    monkeypatch.setattr("strategies.import_module", invalid)
    specs = get_strategies()
    assert "moving_average" in specs
    assert "Syntaxe invalide" in specs["moving_average"]["error"]
    with pytest.raises(ValueError, match="Impossible de charger"):
        specs["moving_average"]["function"](None)
