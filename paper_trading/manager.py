"""Une boucle commune, un portefeuille sauvegardé par stratégie. Aucun ordre réel."""
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Lock, Thread

from backtest.metrics import calculate_max_drawdown, calculate_win_rate
from data.kraken import get_ohlcv, get_ticker
from paper_trading.engine import new_state, process_candle, save_state
from strategies import get_strategies

STATE_DIR = Path(__file__).resolve().parent / "states"


class PaperManager:
    def __init__(self, state_dir=STATE_DIR):
        self.state_dir = state_dir
        self.lock = Lock()
        self.stop_event = Event()
        self.thread = None
        self.portfolios = {}
        if state_dir.exists():
            for path in state_dir.glob("*.json"):
                portfolio = json.loads(path.read_text())
                portfolio["status"] = "paused"  # Reprise explicite après redémarrage.
                self.portfolios[path.stem] = portfolio

    def start_worker(self):
        self.thread = Thread(target=self.loop, daemon=True)
        self.thread.start()

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2)

    def start(self, key, spec, config, approval):
        if ((approval["status"] != "eligible" and approval.get("manual") is not True)
                or approval["source_hash"] != spec["source_hash"]):
            raise ValueError("Cette stratégie n'est pas validée pour le paper.")
        with self.lock:
            existing = self.portfolios.get(key)
            if existing:
                if existing["source_hash"] != spec["source_hash"]:
                    raise ValueError("Code modifié : le portefeuille existant ne peut pas changer de stratégie.")
                previous_config = deepcopy(existing["state"]["config"])
                next_config = deepcopy(config)
                for item in (previous_config, next_config):
                    item.get("market_rules", {}).pop("fetched_at", None)
                if previous_config != next_config:
                    raise ValueError("Configuration différente du portefeuille existant ; conserver les mêmes paramètres.")
                portfolio = deepcopy(existing)
                portfolio.update(status="running", error=None)
                if approval.get("manual"):
                    portfolio["approval"] = deepcopy(approval)
            else:
                state = new_state(deepcopy(config))
                portfolio = {
                    "name": spec["name"], "source_hash": spec["source_hash"], "status": "running",
                    "state": state, "approval": deepcopy(approval), "error": None,
                    "started_at": datetime.now(timezone.utc).isoformat(), "last_poll": None,
                }
            save_state(self.state_dir / f"{key}.json", portfolio)
            self.portfolios[key] = portfolio

    def pause(self, key):
        with self.lock:
            if key not in self.portfolios:
                raise ValueError("Portefeuille paper introuvable.")
            portfolio = deepcopy(self.portfolios[key])
            portfolio["status"] = "paused"
            save_state(self.state_dir / f"{key}.json", portfolio)
            self.portfolios[key] = portfolio

    def snapshot(self):
        with self.lock:
            portfolios = deepcopy(self.portfolios)
        for portfolio in portfolios.values():
            state = portfolio["state"]
            curve = [p["equity"] for p in state.get("equity_curve", [])]
            portfolio["summary"] = {
                "return": state["equity"] / state["config"]["capital"] - 1,
                "max_drawdown": calculate_max_drawdown(curve)["max_drawdown"] if curve else 0,
                "closed_trades": sum(t["side"] == "SELL" for t in state["trades"]),
                "win_rate": calculate_win_rate(state["trades"]),
                "fees": sum(t["fee"] for t in state["trades"]),
                "rejected_orders": len(state.get("rejected_orders", [])),
            }
        return portfolios

    def tick(self, now=None):
        with self.lock:
            active = {key: deepcopy(p) for key, p in self.portfolios.items() if p["status"] == "running"}
        if not active:
            return
        specs = get_strategies()
        market = {}  # Un appel OHLC + ticker par paire/intervalle, partagé entre stratégies.
        for key, portfolio in active.items():
            try:
                spec = specs.get(key)
                if not spec or spec.get("error") or spec["source_hash"] != portfolio["source_hash"]:
                    self.pause(key)
                    raise ValueError("Code de stratégie modifié : paper mis en pause.")
                state = portfolio["state"]
                config = state["config"]
                group = (config["symbol"], config["interval"])
                if group not in market:
                    market[group] = (get_ohlcv(group[0], interval=group[1]), get_ticker(group[0]))
                data, ticker = market[group]
                updated = process_candle(state, data, ticker, now=now, strategy_fn=spec["function"])
                with self.lock:
                    current = self.portfolios[key]
                    if current["status"] != "running" or self.stop_event.is_set():
                        continue
                    current["last_poll"] = datetime.now(timezone.utc).isoformat()
                    current["error"] = None
                    if updated is not state:
                        candidate = {**current, "message": None, "state": updated}
                        save_state(self.state_dir / f"{key}.json", candidate)
                        self.portfolios[key] = candidate
                    else:
                        current["message"] = "En attente d'une nouvelle bougie clôturée récente."
            except Exception as error:
                with self.lock:
                    self.portfolios[key]["error"] = str(error)

    def loop(self):
        while not self.stop_event.is_set():
            self.tick()
            self.stop_event.wait(15)
