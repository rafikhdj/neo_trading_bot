import json

import pandas as pd
import pytest

from backtest.report import DEFAULT_CONFIG, run_strategy
from dashboard.__main__ import BacktestApp
from strategies import get_strategies


@pytest.fixture(autouse=True)
def local_pair_rules(monkeypatch):
    monkeypatch.setattr("dashboard.__main__.get_pair_rules", lambda *args, **kwargs: {
        "ordermin": "0.00006", "costmin": "0.45", "lot_decimals": 8,
        "fetched_at": "2026-11-03T00:00:00+00:00",
    })


def make_history():
    close = list(range(100, 140)) + list(range(140, 100, -1)) * 24
    return pd.DataFrame({"timestamp": pd.date_range("2026-01-01", periods=len(close), freq="5min", tz="UTC"),
                         "open": close, "high": close, "low": close, "close": close, "volume": 1})


@pytest.mark.parametrize("name", ["moving_average", "rsi", "momentum"])
def test_report_matches_portfolio_accounting(name):
    data = make_history()
    result = run_strategy(get_strategies()[name], data, DEFAULT_CONFIG)
    summary = result["summary"]
    assert len(result["equity_curve"]) == len(data)
    assert summary["total_pnl"] == pytest.approx(summary["pnl"])
    assert summary["fees"] == pytest.approx(sum(t["fee"] for t in result["trades"]))
    for trade in result["trades"]:
        assert trade["timestamp"] in set(data["timestamp"])


def test_runner_persists_all_results_and_reloads(tmp_path, monkeypatch):
    monkeypatch.setattr("dashboard.__main__.load_ohlcv", lambda *a: make_history())
    path = tmp_path / "results.json"
    app = BacktestApp(path)
    specs = get_strategies()
    app.state["strategies"] = {key: {"name": s["name"], "status": "pending"} for key, s in specs.items()}
    app.run(specs, DEFAULT_CONFIG.copy(), False)
    result = json.loads(path.read_text())
    assert all(row["status"] == "done" for row in result["strategies"].values())
    assert result["dataset"]["gaps"] == 0
    assert not app.running
    resumed = BacktestApp(path).snapshot()
    assert resumed["strategies"]["rsi"]["result"] == result["strategies"]["rsi"]["result"]


def test_missing_history_is_visible_error(tmp_path, monkeypatch):
    def missing(*args):
        raise ValueError("Pas de données locales")
    monkeypatch.setattr("dashboard.__main__.load_ohlcv", missing)
    app = BacktestApp(tmp_path / "results.json")
    app.state["strategies"] = {"rsi": {"status": "pending"}}
    app.run(get_strategies(), DEFAULT_CONFIG.copy(), False)
    assert app.snapshot()["strategies"]["rsi"]["status"] == "error"
    assert not app.running


def test_paper_api_gate_uses_validation_and_current_code(tmp_path):
    from backtest.validation import load_policy
    app = BacktestApp(tmp_path / "results.json", paper_dir=tmp_path / "paper")
    spec = get_strategies()["moving_average"]
    policy = load_policy()
    app.state["strategies"] = {"moving_average": {
        "name": spec["name"], "status": "done",
        "result": {"summary": {"return": .02, "max_drawdown": .05, "closed_trades": 40}},
        "validation": {"source_hash": spec["source_hash"], "policy": policy,
                       "summary": {"return": .01, "max_drawdown": .03, "closed_trades": 15}},
    }}
    app.start_paper("moving_average")
    assert app.paper.snapshot()["moving_average"]["status"] == "running"
    app.paper.pause("moving_average")
    app.state["strategies"]["moving_average"]["result"]["summary"]["return"] = -.02
    with pytest.raises(ValueError, match="non éligible"):
        app.start_paper("moving_average")
    app.start_paper("moving_average", manual=True)
    assert app.paper.snapshot()["moving_average"]["approval"]["manual"] is True
    app.paper.pause("moving_average")
    from paper_trading.manager import PaperManager
    app.paper = PaperManager(tmp_path / "paper")
    app.start_paper("moving_average", manual=True)
    assert app.paper.snapshot()["moving_average"]["state"]["cash"] == 50
    app.paper.pause("moving_average")
    app.state["strategies"]["moving_average"]["validation"]["source_hash"] = "old code"
    with pytest.raises(ValueError, match="code actuel"):
        app.start_paper("moving_average")
    assert app.paper.snapshot()["moving_average"]["status"] == "paused"


def test_manual_launch_still_requires_current_backtest(tmp_path):
    app = BacktestApp(tmp_path / "results.json", paper_dir=tmp_path / "paper")
    with pytest.raises(ValueError, match="code actuel"):
        app.start_paper("moving_average", manual=True)
    assert app.paper.snapshot() == {}


def test_manual_paper_after_fresh_backtest_saves_numpy_metrics(tmp_path, monkeypatch):
    import json
    import numpy as np
    from paper_trading.manager import PaperManager

    monkeypatch.setattr("dashboard.__main__.load_ohlcv", lambda *args: make_history())
    app = BacktestApp(tmp_path / "results.json", paper_dir=tmp_path / "paper")
    specs = get_strategies()
    app.state["strategies"] = {key: {"name": spec["name"], "status": "pending"}
                                for key, spec in specs.items()}
    app.run(specs, app.state["config"], False)
    assert isinstance(app.state["strategies"]["moving_average"]["result"]["summary"]["return"], np.generic)
    # Pas de redémarrage ni de rechargement JSON entre le calcul et le lancement.
    app.start_paper("moving_average", manual=True)
    saved = json.loads((tmp_path / "paper" / "moving_average.json").read_text())
    assert saved["approval"]["manual"] is True
    assert all(type(check["passed"]) is bool for check in saved["approval"]["checks"])
    assert saved["status"] == "running"
    app.paper = PaperManager(tmp_path / "paper")
    app.start_paper("moving_average", manual=True)
    assert app.paper.snapshot()["moving_average"]["state"]["cash"] == 50
