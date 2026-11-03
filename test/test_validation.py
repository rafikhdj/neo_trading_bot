from backtest.validation import assess, eligibility_for, load_policy
from backtest.engine import backtest
from .test_backtest import make_data


def summary(trades=30, gain=.02, drawdown=.05):
    return {"closed_trades": trades, "return": gain, "max_drawdown": drawdown}


def test_eligibility_requires_both_periods_and_enough_trades():
    policy = load_policy()
    assert assess(summary(), summary(10), policy)["status"] == "eligible"
    assert assess(summary(29), summary(10), policy)["status"] == "insufficient"
    assert assess(summary(), summary(9), policy)["status"] == "insufficient"
    assert assess(summary(), summary(10, gain=0), policy)["status"] == "rejected"
    assert assess(summary(), summary(10, drawdown=.11), policy)["status"] == "rejected"
    assert assess(summary(gain=-.01), summary(10), policy)["status"] == "rejected"


def test_code_and_split_changes_require_another_backtest():
    policy = load_policy()
    row = {"status": "done", "result": {"summary": summary()}, "validation": {
        "summary": summary(10), "source_hash": "tested", "policy": policy,
    }}
    assert eligibility_for(row, {"source_hash": "tested"}, policy)["status"] == "eligible"
    assert eligibility_for(row, {"source_hash": "modified"}, policy)["status"] == "not_tested"
    assert eligibility_for(row, {"source_hash": "tested"}, {**policy, "validation_fraction": .5})["status"] == "not_tested"
    assert eligibility_for(row, {"source_hash": "tested"}, {**policy, "min_closed_trades": 100})["status"] == "insufficient"


def test_validation_warms_indicators_without_trading_the_prefix():
    seen = []
    def decision(history):
        seen.append(len(history))
        return 1 if len(seen) == 1 else -1
    result = backtest(decision, make_data([1, 1, 1, 1, 100, 100, 110]), start_index=4)
    assert seen == [5, 6]
    assert [t["timestamp"] for t in result["trades"]] == [5, 6]
    assert result["capital_final"] == 50.5
    assert len(result["equity_curve"]) == 3
