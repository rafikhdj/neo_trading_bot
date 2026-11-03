import pandas as pd
import pytest

from paper_trading.engine import DEFAULT_CONFIG, new_state, process_candle
from paper_trading.manager import PaperManager
from .test_paper_trading import candles

NOW = pd.Timestamp("2026-11-02 12:30", tz="UTC")


def setup_manager(tmp_path, monkeypatch):
    specs = {
        "first": {"name": "First", "source_hash": "a", "function": lambda data: 1 if data.close.iloc[-1] > data.close.iloc[0] else -1},
        "second": {"name": "Second", "source_hash": "b", "function": lambda data: 1},
    }
    calls = []
    market = {"data": candles()}
    monkeypatch.setattr("paper_trading.manager.get_strategies", lambda: specs)
    def ohlc(*args, **kwargs):
        calls.append("ohlc")
        return market["data"]
    monkeypatch.setattr("paper_trading.manager.get_ohlcv", ohlc)
    monkeypatch.setattr("paper_trading.manager.get_ticker", lambda *a: (calls.append("ticker") or {"ask": 130., "bid": 129., "last": 129.5}))
    manager = PaperManager(tmp_path)
    for key, spec in specs.items():
        manager.start(key, spec, DEFAULT_CONFIG.copy(), {"status": "eligible", "source_hash": spec["source_hash"], "policy": {}})
    return manager, specs, market, calls


def test_independent_portfolios_shared_data_and_no_duplicates(tmp_path, monkeypatch):
    manager, specs, market, calls = setup_manager(tmp_path, monkeypatch)
    manager.tick(now=NOW)
    assert calls == ["ohlc", "ticker"]
    assert all(p["state"]["cash"] == 45 for p in manager.snapshot().values())
    manager.tick(now=NOW)
    assert all(len(p["state"]["trades"]) == 1 for p in manager.snapshot().values())
    market["data"] = candles(False)
    market["data"]["timestamp"] += pd.Timedelta(minutes=5)
    manager.tick(now=NOW + pd.Timedelta(minutes=5))
    state = manager.snapshot()
    assert state["first"]["state"]["quantity"] == 0
    assert state["second"]["state"]["quantity"] > 0
    assert state["first"]["summary"]["closed_trades"] == 1
    assert state["second"]["summary"]["closed_trades"] == 0
    # Les nouveaux objets reprennent les soldes, en pause, sans appel réseau.
    before = len(calls)
    resumed = PaperManager(tmp_path)
    resumed.tick(now=NOW)
    assert len(calls) == before
    assert all(p["status"] == "paused" for p in resumed.snapshot().values())
    assert resumed.snapshot()["first"]["state"] == state["first"]["state"]


def test_modified_strategy_pauses_and_cannot_resume_old_portfolio(tmp_path, monkeypatch):
    manager, specs, market, calls = setup_manager(tmp_path, monkeypatch)
    specs["first"]["source_hash"] = "new"
    manager.tick(now=NOW)
    assert manager.snapshot()["first"]["status"] == "paused"
    assert manager.snapshot()["second"]["state"]["quantity"] > 0
    with pytest.raises(ValueError, match="Code modifié"):
        manager.start("first", specs["first"], DEFAULT_CONFIG.copy(), {"status": "eligible", "source_hash": "new"})


def test_unvalidated_strategy_cannot_start(tmp_path):
    manager = PaperManager(tmp_path)
    with pytest.raises(ValueError, match="pas validée"):
        manager.start("first", {"source_hash": "a"}, DEFAULT_CONFIG.copy(), {"status": "rejected", "source_hash": "a"})
    assert manager.snapshot() == {}


def test_paper_respects_minimums_and_strategy_selection():
    config = {**DEFAULT_CONFIG, "market_rules": {"ordermin": "1", "costmin": "0.45", "lot_decimals": 8}}
    state = process_candle(new_state(config), candles(), {"ask": 130., "bid": 129., "last": 129.5},
                           now=NOW, strategy_fn=lambda data: 1)
    assert state["cash"] == 50
    assert state["quantity"] == 0
    assert len(state["rejected_orders"]) == 1


def test_one_strategy_failure_does_not_block_the_other(tmp_path, monkeypatch):
    manager, specs, market, calls = setup_manager(tmp_path, monkeypatch)
    def broken(data):
        raise ValueError("Erreur de stratégie")
    specs["first"]["function"] = broken
    manager.tick(now=NOW)
    state = manager.snapshot()
    assert "Erreur de stratégie" in state["first"]["error"]
    assert state["first"]["state"]["cash"] == 50
    assert state["second"]["state"]["quantity"] > 0


def test_failed_save_does_not_apply_trade_in_memory(tmp_path, monkeypatch):
    manager, specs, market, calls = setup_manager(tmp_path, monkeypatch)
    def no_disk(*args):
        raise OSError("Disque indisponible")
    monkeypatch.setattr("paper_trading.manager.save_state", no_disk)
    manager.tick(now=NOW)
    for p in manager.snapshot().values():
        assert p["state"]["cash"] == 50
        assert p["state"]["trades"] == []
        assert "Disque indisponible" in p["error"]


def test_discovering_another_strategy_keeps_existing_paper_running(tmp_path, monkeypatch):
    manager, specs, market, calls = setup_manager(tmp_path, monkeypatch)
    manager.tick(now=NOW)
    before = manager.snapshot()
    specs["new_strategy"] = {"name": "Nouvelle", "source_hash": "new", "function": lambda data: 1}
    manager.tick(now=NOW)
    after = manager.snapshot()
    assert set(after) == {"first", "second"}  # Aucune promotion automatique.
    for key in before:
        assert after[key]["status"] == "running"
        assert after[key]["started_at"] == before[key]["started_at"]
        assert after[key]["source_hash"] == before[key]["source_hash"]
        assert after[key]["state"] == before[key]["state"]
