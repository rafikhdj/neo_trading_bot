"""Même moteur et mêmes hypothèses pour toutes les stratégies."""
from backtest.engine import backtest
from backtest.metrics import calculate_max_drawdown, calculate_pnl, calculate_win_rate

DEFAULT_CONFIG = {
    "symbol": "XBTEUR", "interval": 5, "capital": 50.0,
    "position_size": 0.10, "fee_rate": 0.0025, "slippage_rate": 0.001,
}


def run_strategy(spec, data, config, start_index=0):
    if len(data) < spec["warmup"] + 1:
        raise ValueError(f"Au moins {spec['warmup'] + 1} bougies nécessaires.")
    result = backtest(spec["function"], data, capital=config["capital"],
                      fee_rate=config["fee_rate"], slippage_rate=config["slippage_rate"],
                      position_size=config["position_size"], market_rules=config.get("market_rules"),
                      start_index=start_index)
    curve = [point["equity"] for point in result["equity_curve"]]
    pnl = calculate_pnl(result["trades"], float(data.iloc[-1]["close"]))
    result["summary"] = {
        "initial_capital": config["capital"], "final_equity": curve[-1],
        "pnl": curve[-1] - config["capital"],
        "return": curve[-1] / config["capital"] - 1,
        "max_drawdown": calculate_max_drawdown(curve)["max_drawdown"],
        "win_rate": calculate_win_rate(result["trades"]),
        "closed_trades": result["nb_sell"], "open_position": result["nb_buy"] > result["nb_sell"],
        "fees": result["total_fees"], **pnl,
        "rejected_orders": len(result["rejected_orders"]),
        "buy_hold_return": float(data.iloc[-1]["close"] / data.iloc[start_index]["close"] - 1),
    }
    return result


def validate_strategy(spec, data, config, full_result, policy):
    from backtest.validation import assess

    split = max(1, int(len(data) * (1 - policy["validation_fraction"])))
    # Les premières bougies servent d'amorce aux indicateurs, jamais à des ordres.
    # Le portefeuille de validation repart à zéro à la frontière chronologique.
    validation = run_strategy(spec, data, config, start_index=split - 1)
    return {
        **assess(full_result["summary"], validation["summary"], policy),
        "policy": policy, "summary": validation["summary"],
        "start": data.iloc[split]["timestamp"], "end": data.iloc[-1]["timestamp"],
        "source_hash": spec["source_hash"],
    }
