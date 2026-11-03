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


def test_network_incident_is_durable_and_creates_no_trade(tmp_path, monkeypatch):
    import json
    import requests
    manager, specs, market, calls = setup_manager(tmp_path, monkeypatch)
    def offline(*args, **kwargs):
        raise requests.ConnectionError("Réseau indisponible")
    monkeypatch.setattr("paper_trading.manager.get_ohlcv", offline)
    manager.tick(now=NOW)
    for p in manager.snapshot().values():
        assert p["state"]["trades"] == []
        assert p["state"]["cash"] == 50
        assert p["error"] == "Réseau indisponible"
        assert p["events"][-1]["type"] == "error"
    # Même erreur active : ne duplique pas l'incident à chaque cycle.
    manager.tick(now=NOW)
    assert len(manager.snapshot()["first"]["events"]) == 2  # start + error
    restarted = PaperManager(tmp_path).snapshot()
    for key, p in restarted.items():
        assert p["status"] == "paused"
        assert p["error"] == "Réseau indisponible"
        assert p["events"][-1]["type"] == "pause"
        assert json.loads((tmp_path / f"{key}.json").read_text())["status"] == "paused"


def test_pause_and_changed_code_reason_survive_restart(tmp_path, monkeypatch):
    manager, specs, market, calls = setup_manager(tmp_path, monkeypatch)
    manager.pause("second")
    specs["first"]["source_hash"] = "changed"
    manager.tick(now=NOW)
    restarted = PaperManager(tmp_path).snapshot()
    assert restarted["second"]["events"][-1]["message"] == "Pause manuelle."
    assert "Code de stratégie modifié" in restarted["first"]["error"]
    assert all(p["status"] == "paused" for p in restarted.values())
    assert all(not p["state"]["trades"] for p in restarted.values())


def test_recovery_clears_current_error_but_keeps_event_history(tmp_path, monkeypatch):
    import json
    manager, specs, market, calls = setup_manager(tmp_path, monkeypatch)
    original = __import__('paper_trading.manager', fromlist=['get_ohlcv']).get_ohlcv
    def fail(*args, **kwargs):
        raise RuntimeError("Lecture impossible")
    monkeypatch.setattr("paper_trading.manager.get_ohlcv", fail)
    manager.tick(now=NOW)
    monkeypatch.setattr("paper_trading.manager.get_ohlcv", original)
    manager.tick(now=NOW)
    saved = json.loads((tmp_path / "first.json").read_text())
    assert saved["error"] is None
    assert [event["type"] for event in saved["events"]] == ["start", "error", "recovery"]
    assert len(saved["state"]["trades"]) == 1
    manager.tick(now=NOW)
    assert len(manager.snapshot()["first"]["state"]["trades"]) == 1


def test_network_error_is_not_masked_when_incident_save_also_fails(tmp_path, monkeypatch, caplog):
    from paper_trading.manager import PaperPersistenceError
    manager, specs, market, calls = setup_manager(tmp_path, monkeypatch)
    def offline(*args, **kwargs):
        raise RuntimeError("Erreur réseau initiale")
    def no_disk(*args):
        raise OSError("Disque plein")
    monkeypatch.setattr("paper_trading.manager.get_ohlcv", offline)
    monkeypatch.setattr("paper_trading.manager.save_state", no_disk)
    manager.tick(now=NOW)
    p = manager.snapshot()["first"]
    assert p["status"] == "paused"
    assert p["error"] == "Erreur réseau initiale"
    assert p["persistence_error"] == "Disque plein"
    assert p["incident_save_error"] == "Disque plein"
    assert p["persistence_uncertain"]
    assert not p["state"]["trades"]
    assert "Erreur réseau initiale" in caplog.text and "Disque plein" in caplog.text
    with pytest.raises(PaperPersistenceError):
        manager.start("first", specs["first"], DEFAULT_CONFIG.copy(),
                      {"status": "eligible", "source_hash": "a"})


def test_successful_replace_then_exception_never_repeats_a_trade(tmp_path, monkeypatch):
    from paper_trading.engine import save_state as real_save
    manager, specs, market, calls = setup_manager(tmp_path, monkeypatch)
    failed = set()
    def after_replace(path, portfolio):
        real_save(path, portfolio)
        if portfolio["state"]["trades"] and path.stem not in failed:
            failed.add(path.stem)
            raise OSError("Erreur après remplacement")
    monkeypatch.setattr("paper_trading.manager.save_state", after_replace)
    manager.tick(now=NOW)
    assert all(p["status"] == "paused" for p in manager.snapshot().values())
    assert all(len(p["state"]["trades"]) == 1 for p in manager.snapshot().values())
    manager.tick(now=NOW)  # Aucun nouvel essai automatique sur la bougie.
    restarted = PaperManager(tmp_path)
    restarted.start("first", specs["first"], DEFAULT_CONFIG.copy(),
                    {"status": "eligible", "source_hash": "a"})
    restarted.tick(now=NOW)
    assert len(restarted.snapshot()["first"]["state"]["trades"]) == 1
    assert any("Erreur après remplacement" in e["message"]
               for e in restarted.snapshot()["first"]["events"])


def test_interruption_processes_only_latest_candle_after_resume(tmp_path, monkeypatch):
    manager, specs, market, calls = setup_manager(tmp_path, monkeypatch)
    manager.tick(now=NOW)
    manager.pause("first")
    # Interruption d'une heure : ne pas simuler 12 exécutions rétrospectives.
    market["data"] = candles(False)
    market["data"]["timestamp"] += pd.Timedelta(hours=1)
    restarted = PaperManager(tmp_path)
    restarted.tick(now=NOW + pd.Timedelta(hours=1))
    assert all(len(p["state"]["trades"]) == 1 for p in restarted.snapshot().values())
    restarted.start("first", specs["first"], DEFAULT_CONFIG.copy(),
                    {"status": "eligible", "source_hash": "a"})
    restarted.tick(now=NOW + pd.Timedelta(hours=1))
    p = restarted.snapshot()["first"]
    assert [t["side"] for t in p["state"]["trades"]] == ["BUY", "SELL"]
    assert p["state"]["trades"][-1]["candle"] == market["data"].timestamp.iloc[-1].isoformat()
    assert len(p["state"]["equity_curve"]) == 3  # initial + 2 décisions, pas les bougies manquées


def test_inflight_calculation_is_discarded_after_pause_and_resume(tmp_path, monkeypatch):
    manager, specs, market, calls = setup_manager(tmp_path, monkeypatch)
    def pause_during_signal(data):
        manager.pause("first")
        manager.start("first", specs["first"], DEFAULT_CONFIG.copy(),
                      {"status": "eligible", "source_hash": "a"})
        return 1
    specs["first"]["function"] = pause_during_signal
    manager.tick(now=NOW)
    assert manager.snapshot()["first"]["state"]["trades"] == []
    assert len(manager.snapshot()["second"]["state"]["trades"]) == 1


def test_strategy_discovery_failure_is_persisted_without_killing_loop(tmp_path, monkeypatch):
    manager, specs, market, calls = setup_manager(tmp_path, monkeypatch)
    def broken():
        raise RuntimeError("Registre indisponible")
    monkeypatch.setattr("paper_trading.manager.get_strategies", broken)
    manager.tick(now=NOW)
    restarted = PaperManager(tmp_path).snapshot()
    assert all(p["status"] == "paused" and p["error"] == "Registre indisponible" for p in restarted.values())
    assert not calls


def test_concurrent_ticks_commit_each_candle_once(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    manager, specs, market, calls = setup_manager(tmp_path, monkeypatch)
    barrier = Barrier(2)
    def simultaneous(data):
        barrier.wait(timeout=3)
        return 1
    specs["first"]["function"] = simultaneous
    before = manager.snapshot()
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: manager.tick(now=NOW), range(2)))
    for key, p in manager.snapshot().items():
        assert len(p["state"]["trades"]) == 1
        assert p["revision"] == before[key]["revision"] + 1
        assert p["state"]["cash"] == 45


def test_unreadable_state_after_write_error_blocks_resume_and_further_writes(tmp_path, monkeypatch):
    import json
    from paper_trading.engine import save_state as real_save
    from paper_trading.manager import PaperPersistenceError
    manager, specs, market, calls = setup_manager(tmp_path, monkeypatch)
    writes = []
    def fail_first(path, portfolio):
        writes.append(path.stem)
        if path.stem == "first":
            raise OSError("Écriture interrompue")
        real_save(path, portfolio)
    def unreadable(*args):
        raise ValueError("État impossible à confirmer")
    monkeypatch.setattr("paper_trading.manager.save_state", fail_first)
    monkeypatch.setattr(json, "loads", unreadable)
    manager.tick(now=NOW)
    assert manager.snapshot()["first"]["persistence_uncertain"]
    assert manager.snapshot()["second"]["status"] == "running"
    count = len(writes)
    with pytest.raises(PaperPersistenceError):
        manager.pause("first")
    with pytest.raises(PaperPersistenceError):
        manager.start("first", specs["first"], DEFAULT_CONFIG.copy(), {"status": "eligible", "source_hash": "a"})
    assert len(writes) == count  # Ne jamais écraser un état durable non confirmé.


def test_startup_write_failure_still_leaves_every_strategy_paused(tmp_path, monkeypatch):
    from paper_trading.engine import save_state as real_save
    manager, specs, market, calls = setup_manager(tmp_path, monkeypatch)
    manager.tick(now=NOW)
    def failed(*args):
        raise OSError("Disque indisponible au démarrage")
    monkeypatch.setattr("paper_trading.manager.save_state", failed)
    restarted = PaperManager(tmp_path)
    restarted.tick(now=NOW)
    for p in restarted.snapshot().values():
        assert p["status"] == "paused" and p["persistence_uncertain"]
        assert len(p["state"]["trades"]) == 1
    monkeypatch.setattr("paper_trading.manager.save_state", real_save)
    recovered = PaperManager(tmp_path)
    assert all(p["status"] == "paused" and len(p["state"]["trades"]) == 1
               for p in recovered.snapshot().values())
