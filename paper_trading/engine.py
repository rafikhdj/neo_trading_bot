"""Simulation spot uniquement : aucune clé API ni envoi d'ordre."""
import argparse
from copy import deepcopy
from decimal import Decimal, ROUND_DOWN
import json
import logging
from pathlib import Path
import time

import pandas as pd

from backtest.engine import order_rejection
from strategies.moving_average import strategy

LOG = logging.getLogger(__name__)
DEFAULT_CONFIG = {
    "symbol": "XBTEUR", "interval": 5, "capital": 50,
    "position_size": 0.10, "fee_rate": 0.0025, "slippage_rate": 0.001,
}


def load_state(path, config):
    if path.exists():
        state = json.loads(path.read_text())
        if state["config"] != config:
            raise ValueError("Configuration différente de la sauvegarde : utiliser un autre --state.")
        return state
    return new_state(config)


def new_state(config):
    return {"config": config, "cash": config["capital"], "quantity": 0.0,
            "last_candle": None, "trades": [], "equity": config["capital"], "pnl": 0.0}


def save_state(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2))
    temporary.replace(path)


def process_candle(state, data, ticker, now=None, strategy_fn=None):
    """Traite uniquement la dernière clôture ; ne rejoue pas les ordres manqués."""
    if data.empty:
        return state
    config = state["config"]
    candle_time = pd.Timestamp(data.iloc[-1]["timestamp"])
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    age = (now - candle_time).total_seconds()
    seconds = config["interval"] * 60
    if not seconds <= age < 2 * seconds:
        LOG.warning("Bougies trop anciennes ou non clôturées : aucun traitement")
        return state
    if state["last_candle"] is not None and candle_time <= pd.Timestamp(state["last_candle"]):
        return state

    updated = deepcopy(state)
    signal = (strategy_fn or strategy)(data)
    fee_rate = config["fee_rate"]
    slippage = config["slippage_rate"]
    trade = None
    if signal == 1 and updated["quantity"] == 0:
        price = ticker["ask"] * (1 + slippage)
        budget = updated["cash"] * config["position_size"]
        quantity = budget / (price * (1 + fee_rate))
        rules = config.get("market_rules")
        if rules:
            quantum = Decimal(1).scaleb(-rules["lot_decimals"])
            quantity = float(Decimal(str(quantity)).quantize(quantum, rounding=ROUND_DOWN))
        reason = order_rejection(quantity, price, rules)
        if reason:
            updated.setdefault("rejected_orders", []).append({
                "timestamp": now.isoformat(), "side": "BUY", "price": price,
                "quantity": quantity, "reason": reason,
            })
        else:
            fee = quantity * price * fee_rate
            updated["cash"] -= quantity * price + fee
            updated["quantity"] = quantity
            trade = {"side": "BUY", "price": price, "quantity": quantity, "fee": fee}
    elif signal == -1 and updated["quantity"] > 0:
        price = ticker["bid"] * (1 - slippage)
        quantity = updated["quantity"]
        reason = order_rejection(quantity, price, config.get("market_rules"))
        if reason:
            updated.setdefault("rejected_orders", []).append({
                "timestamp": now.isoformat(), "side": "SELL", "price": price,
                "quantity": quantity, "reason": reason,
            })
        else:
            fee = quantity * price * fee_rate
            updated["cash"] += quantity * price - fee
            updated["quantity"] = 0.0
            trade = {"side": "SELL", "price": price, "quantity": quantity, "fee": fee}
    if trade is not None:
        trade.update(timestamp=now.isoformat(), candle=candle_time.isoformat())
        updated["trades"].append(trade)
    updated["last_candle"] = candle_time.isoformat()
    updated["equity"] = updated["cash"] + updated["quantity"] * ticker["last"]
    updated["pnl"] = updated["equity"] - config["capital"]
    curve = updated.setdefault("equity_curve", [])
    if not curve:
        curve.append({"timestamp": now.isoformat(), "equity": config["capital"]})
    curve.append({"timestamp": now.isoformat(), "equity": updated["equity"]})
    updated["updated_at"] = now.isoformat()
    return updated


def main():
    from backtest.validation import eligibility_for, load_policy
    from paper_trading.manager import PaperManager
    from strategies import get_strategies

    specs = get_strategies()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy", required=True, choices=list(specs))
    parser.add_argument("--once", action="store_true", help="Un seul passage puis arrêt")
    args = parser.parse_args()
    path = Path(__file__).resolve().parent.parent / "backtest_results.json"
    if not path.exists():
        parser.error("Lancer les backtests dans le dashboard avant le paper.")
    results = json.loads(path.read_text())
    approval = eligibility_for(results["strategies"].get(args.strategy, {}), specs[args.strategy], load_policy())
    if approval["status"] != "eligible":
        parser.error("Stratégie non éligible : consulter les critères dans le dashboard.")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    manager = PaperManager()
    manager.start(args.strategy, specs[args.strategy], results["config"], approval)
    try:
        while True:
            manager.tick()
            portfolio = manager.snapshot()[args.strategy]
            LOG.info("%s | %s | portefeuille %.2f EUR | P&L %.2f EUR | %s", args.strategy,
                     portfolio["status"], portfolio["state"]["equity"], portfolio["state"]["pnl"],
                     portfolio.get("error") or "OK")
            if args.once:
                break
            time.sleep(15)
    except KeyboardInterrupt:
        pass
    finally:
        manager.pause(args.strategy)


if __name__ == "__main__":
    main()
