"""Une boucle commune, un portefeuille sauvegardé par stratégie. Aucun ordre réel."""
import json
import logging
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Lock, Thread

from backtest.metrics import calculate_max_drawdown, calculate_win_rate
from data.kraken import get_ohlcv, get_ticker
from paper_trading.engine import new_state, process_candle, save_state
from strategies import get_strategies

LOG = logging.getLogger(__name__)


class PaperPersistenceError(ValueError):
    """Sauvegarde incertaine : stratégie mise en pause jusqu’à résolution."""


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
                portfolio = json.loads(path.read_text(encoding="utf-8"))
                self.portfolios[path.stem] = portfolio
                if portfolio["status"] != "paused":
                    candidate = deepcopy(portfolio)
                    candidate["status"] = "paused"
                    self._event(candidate, "pause", "Redémarrage : reprise manuelle requise.")
                    try:
                        self._commit(path.stem, candidate)
                    except PaperPersistenceError:
                        pass  # L'état reste bloqué en pause et l'incident est journalisé.

    @staticmethod
    def _event(portfolio, kind, message):
        portfolio.setdefault("events", []).append({
            "timestamp": datetime.now(timezone.utc).isoformat(), "type": kind, "message": message,
        })

    def _commit(self, key, candidate):
        """Appelé sous self.lock (ou à l'initialisation, avant les threads)."""
        path = self.state_dir / f"{key}.json"
        candidate["revision"] = self.portfolios.get(key, {}).get("revision", 0) + 1
        try:
            save_state(path, candidate)
        except Exception as write_error:
            # Une exception peut survenir APRES le remplacement : ne jamais réécrire
            # aveuglément l'ancien portefeuille par-dessus une transaction validée.
            try:
                durable = json.loads(path.read_text(encoding="utf-8"))
                required = {"config", "cash", "quantity", "last_candle", "trades", "equity", "pnl"}
                if (not isinstance(durable.get("state"), dict)
                        or not required.issubset(durable["state"])
                        or durable.get("source_hash") != candidate["source_hash"]
                        or durable.get("started_at") != candidate["started_at"]):
                    raise ValueError("État sur disque non reconnu.")
            except FileNotFoundError:
                durable = deepcopy(candidate) if key not in self.portfolios else None
            except Exception:
                durable = None
            recovered = deepcopy(durable if durable is not None else self.portfolios.get(key, candidate))
            recovered.update(status="paused", error=candidate.get("error") or str(write_error),
                             persistence_error=str(write_error), revision=candidate["revision"] + 1,
                             message="Échec de sauvegarde : stratégie suspendue, reprise manuelle requise.")
            recovered["events"] = deepcopy(candidate.get("events", []))
            self._event(recovered, "persistence_error", str(write_error))
            self._event(recovered, "pause", "Sauvegarde incertaine : exécution suspendue.")
            recovered["persistence_uncertain"] = durable is None
            if durable is not None:
                try:
                    save_state(path, recovered)
                except Exception as incident_error:
                    recovered["persistence_uncertain"] = True
                    recovered["incident_save_error"] = str(incident_error)
            self.portfolios[key] = recovered
            message = f"{recovered['error']} ; sauvegarde : {write_error}"
            if recovered.get("incident_save_error"):
                message += f" ; sauvegarde de l'incident : {recovered['incident_save_error']}"
            LOG.error("Paper %s mis en pause : %s", key, message)
            raise PaperPersistenceError(message) from write_error
        self.portfolios[key] = candidate

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
                if existing.get("persistence_uncertain"):
                    raise PaperPersistenceError("État incertain : rétablir le disque puis redémarrer pour relire l'état durable.")
                if existing["source_hash"] != spec["source_hash"]:
                    raise ValueError("Code modifié : le portefeuille existant ne peut pas changer de stratégie.")
                previous_config = deepcopy(existing["state"]["config"])
                next_config = deepcopy(config)
                for item in (previous_config, next_config):
                    item.get("market_rules", {}).pop("fetched_at", None)
                if previous_config != next_config:
                    raise ValueError("Configuration différente du portefeuille existant ; conserver les mêmes paramètres.")
                portfolio = deepcopy(existing)
                portfolio.update(status="running", error=None, persistence_error=None, message=None)
                portfolio.pop("incident_save_error", None)
                if approval.get("manual"):
                    portfolio["approval"] = deepcopy(approval)
            else:
                state = new_state(deepcopy(config))
                portfolio = {
                    "name": spec["name"], "source_hash": spec["source_hash"], "status": "running",
                    "state": state, "approval": deepcopy(approval), "error": None,
                    "started_at": datetime.now(timezone.utc).isoformat(), "last_poll": None,
                }
            self._event(portfolio, "resume" if existing else "start", "Lancement manuel du paper.")
            self._commit(key, portfolio)

    def pause(self, key, reason="Pause manuelle.", error=None):
        with self.lock:
            if key not in self.portfolios:
                raise ValueError("Portefeuille paper introuvable.")
            if self.portfolios[key].get("persistence_uncertain"):
                raise PaperPersistenceError("Déjà en pause : rétablir le disque puis redémarrer pour confirmer l’état.")
            portfolio = deepcopy(self.portfolios[key])
            portfolio["status"] = "paused"
            if error is not None:
                portfolio["error"] = error
            self._event(portfolio, "pause", reason)
            self._commit(key, portfolio)

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
        try:
            specs = get_strategies()
        except Exception as error:
            for key in active:
                try:
                    self.pause(key, reason="Chargement des stratégies impossible.", error=str(error))
                except PaperPersistenceError:
                    pass
            return
        market = {}  # Un appel OHLC + ticker par paire/intervalle, partagé entre stratégies.
        for key, portfolio in active.items():
            try:
                spec = specs.get(key)
                if not spec or spec.get("error") or spec["source_hash"] != portfolio["source_hash"]:
                    reason = "Code de stratégie modifié : paper mis en pause."
                    self.pause(key, reason=reason, error=reason)
                    continue
                state = portfolio["state"]
                config = state["config"]
                group = (config["symbol"], config["interval"])
                if group not in market:
                    market[group] = (get_ohlcv(group[0], interval=group[1]), get_ticker(group[0]))
                data, ticker = market[group]
                updated = process_candle(state, data, ticker, now=now, strategy_fn=spec["function"])
                with self.lock:
                    current = self.portfolios[key]
                    if (current["status"] != "running" or self.stop_event.is_set()
                            or current.get("revision", 0) != portfolio.get("revision", 0)):
                        continue  # Pause/reprise ou autre tick validé pendant le calcul.
                    candidate = deepcopy(current)
                    candidate["last_poll"] = datetime.now(timezone.utc).isoformat()
                    candidate["message"] = None if updated is not state else "En attente d'une nouvelle bougie clôturée récente."
                    if current.get("error"):
                        candidate["error"] = None
                        self._event(candidate, "recovery", "Traitement du paper rétabli.")
                    if updated is not state or current.get("error"):
                        candidate["state"] = updated
                        self._commit(key, candidate)
                    else:
                        self.portfolios[key] = candidate  # Heartbeat seul : pas d'écriture inutile.
            except PaperPersistenceError:
                pass  # _commit a déjà bloqué l'exécution et conservé les causes.
            except Exception as error:
                with self.lock:
                    current = self.portfolios[key]
                    if current.get("error") == str(error):
                        continue  # Même incident toujours actif, déjà sauvegardé.
                    candidate = deepcopy(current)
                    candidate["error"] = str(error)
                    self._event(candidate, "error", str(error))
                    try:
                        self._commit(key, candidate)
                    except PaperPersistenceError:
                        pass

    def loop(self):
        while not self.stop_event.is_set():
            self.tick()
            self.stop_event.wait(15)
