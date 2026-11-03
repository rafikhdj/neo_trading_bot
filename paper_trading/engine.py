"""Simulation spot uniquement : aucune clé API ni envoi d'ordre."""
import argparse
from copy import deepcopy
import json
import logging
from pathlib import Path
import time

import pandas as pd
import requests

from data.kraken import get_ohlcv, get_ticker
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
    return {"config": config, "cash": config["capital"], "quantity": 0.0,
            "last_candle": None, "trades": [], "equity": config["capital"], "pnl": 0.0}


def save_state(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2))
    temporary.replace(path)


def process_candle(state, data, ticker, now=None):
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
    signal = strategy(data)
    fee_rate = config["fee_rate"]
    slippage = config["slippage_rate"]
    trade = None
    if signal == 1 and updated["quantity"] == 0:
        price = ticker["ask"] * (1 + slippage)
        budget = updated["cash"] * config["position_size"]
        quantity = budget / (price * (1 + fee_rate))
        fee = quantity * price * fee_rate
        updated["cash"] -= budget
        updated["quantity"] = quantity
        trade = {"side": "BUY", "price": price, "quantity": quantity, "fee": fee}
    elif signal == -1 and updated["quantity"] > 0:
        price = ticker["bid"] * (1 - slippage)
        quantity = updated["quantity"]
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
    return updated


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Un seul passage puis arrêt")
    parser.add_argument("--state", type=Path, default=Path("paper_state.json"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    state = load_state(args.state, DEFAULT_CONFIG.copy())
    LOG.info("Simulation %s | capital initial %.2f EUR", state["config"]["symbol"], state["config"]["capital"])
    try:
        while True:
            try:
                config = state["config"]
                data = get_ohlcv(config["symbol"], interval=config["interval"])
                ticker = get_ticker(config["symbol"])
                updated = process_candle(state, data, ticker)
                if updated is not state:
                    save_state(args.state, updated)
                    action = (updated["trades"][-1]["side"]
                              if len(updated["trades"]) > len(state["trades"]) else "HOLD")
                    state = updated
                    LOG.info("%s | cash %.2f | BTC %.8f | portefeuille %.2f EUR | P&L %.2f EUR",
                             action, state["cash"], state["quantity"], state["equity"], state["pnl"])
            except (requests.RequestException, RuntimeError) as error:
                if args.once:
                    raise
                LOG.warning("Erreur Kraken, nouvel essai dans 15 s : %s", error)
            if args.once:
                break
            time.sleep(15)
    except KeyboardInterrupt:
        LOG.info("Simulation arrêtée ; état sauvegardé après chaque bougie traitée.")


if __name__ == "__main__":
    main()
