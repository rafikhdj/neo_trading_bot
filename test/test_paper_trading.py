import pandas as pd
import pytest

from paper_trading.engine import DEFAULT_CONFIG, load_state, process_candle, save_state


def candles(rising=True):
    return pd.DataFrame({
        "timestamp": pd.date_range("2026-11-02 10:00", periods=30, freq="5min", tz="UTC"),
        "close": list(range(100, 130)) if rising else list(range(130, 100, -1)),
    })


def test_buy_save_resume_and_sell(tmp_path):
    path = tmp_path / "state.json"
    state = load_state(path, DEFAULT_CONFIG.copy())
    ticker = {"ask": 130.0, "bid": 129.0, "last": 129.5}
    now = pd.Timestamp("2026-11-02 12:30", tz="UTC")
    bought = process_candle(state, candles(), ticker, now)
    assert state["cash"] == 50  # L'état précédent reste intact avant sauvegarde.
    assert bought["cash"] == 45
    buy = bought["trades"][0]
    assert buy["price"] == pytest.approx(130 * 1.001)
    assert buy["quantity"] * buy["price"] + buy["fee"] == pytest.approx(5)
    save_state(path, bought)
    resumed = load_state(path, DEFAULT_CONFIG.copy())
    assert process_candle(resumed, candles(), ticker, now) is resumed
    data = candles(False)
    data["timestamp"] += pd.Timedelta(minutes=5)
    sold = process_candle(resumed, data, ticker, now + pd.Timedelta(minutes=5))
    sell = sold["trades"][-1]
    assert sell["side"] == "SELL"
    assert sell["price"] == pytest.approx(129 * 0.999)
    assert sold["quantity"] == 0
    assert sold["cash"] == pytest.approx(45 + sell["quantity"] * sell["price"] - sell["fee"])
    assert sold["pnl"] == pytest.approx(sold["cash"] - 50)


def test_stale_data_does_not_trade(tmp_path):
    state = load_state(tmp_path / "state.json", DEFAULT_CONFIG.copy())
    assert process_candle(state, candles(), {}, pd.Timestamp("2026-11-02 14:00", tz="UTC")) is state


def test_same_signal_does_not_buy_twice(tmp_path):
    state = load_state(tmp_path / "state.json", DEFAULT_CONFIG.copy())
    ticker = {"ask": 130., "bid": 129., "last": 129.5}
    now = pd.Timestamp("2026-11-02 12:30", tz="UTC")
    bought = process_candle(state, candles(), ticker, now)
    data = candles()
    data["timestamp"] += pd.Timedelta(minutes=5)
    held = process_candle(bought, data, ticker, now + pd.Timedelta(minutes=5))
    assert len(held["trades"]) == 1
    assert held["cash"] == bought["cash"]


def test_reject_different_configuration(tmp_path):
    path = tmp_path / "state.json"
    save_state(path, load_state(path, DEFAULT_CONFIG.copy()))
    with pytest.raises(ValueError, match="Configuration"):
        load_state(path, {**DEFAULT_CONFIG, "symbol": "ETHEUR"})


def test_equity_and_pnl_include_position_and_both_fees(tmp_path):
    state = load_state(tmp_path / "state.json", DEFAULT_CONFIG.copy())
    quote = {"ask": 130., "bid": 129., "last": 129.5}
    now = pd.Timestamp("2026-11-02 12:30", tz="UTC")
    bought = process_candle(state, candles(), quote, now=now, strategy_fn=lambda data: 1)
    buy = bought["trades"][0]
    assert bought["cash"] + buy["quantity"] * buy["price"] + buy["fee"] == pytest.approx(50)
    assert bought["equity"] == pytest.approx(bought["cash"] + bought["quantity"] * quote["last"])
    assert bought["pnl"] == pytest.approx(bought["equity"] - 50)
    data = candles()
    data["timestamp"] += pd.Timedelta(minutes=5)
    sold = process_candle(bought, data, quote, now=now + pd.Timedelta(minutes=5), strategy_fn=lambda data: -1)
    sell = sold["trades"][-1]
    assert sold["quantity"] == 0
    assert sold["equity"] == pytest.approx(sold["cash"])
    assert sold["pnl"] == pytest.approx((sell["price"] - buy["price"]) * buy["quantity"] - buy["fee"] - sell["fee"])


def test_failed_atomic_replace_preserves_previous_state_and_cleans_temp(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    save_state(path, {"version": 1})
    def fail(*args):
        raise OSError("Remplacement refusé")
    monkeypatch.setattr("paper_trading.engine.os.replace", fail)
    with pytest.raises(OSError, match="Remplacement refusé"):
        save_state(path, {"version": 2})
    import json
    assert json.loads(path.read_text()) == {"version": 1}
    assert list(tmp_path.glob("*.tmp")) == []


def test_temp_cleanup_error_never_masks_replace_error(tmp_path, monkeypatch, caplog):
    from pathlib import Path
    def replace_failed(*args):
        raise OSError("Erreur initiale de remplacement")
    def cleanup_failed(*args, **kwargs):
        raise OSError("Erreur secondaire de nettoyage")
    monkeypatch.setattr("paper_trading.engine.os.replace", replace_failed)
    monkeypatch.setattr(Path, "unlink", cleanup_failed)
    with pytest.raises(OSError, match="Erreur initiale de remplacement"):
        save_state(tmp_path / "state.json", {"cash": 50})
    assert "erreur initiale est conservée" in caplog.text
