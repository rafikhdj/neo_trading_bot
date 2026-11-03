"""Lancer avec python -m dashboard, puis ouvrir http://127.0.0.1:8000."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Lock, Thread
from urllib.parse import urlsplit


from backtest.report import DEFAULT_CONFIG, run_strategy, validate_strategy
from backtest.validation import load_policy, eligibility_for
from paper_trading.manager import PaperManager
from data.kraken import get_ohlcv, load_ohlcv, get_pair_rules
from data.history import repair_history
from strategies import get_strategies

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT.parent / "backtest_results.json"


def json_default(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    raise TypeError(f"Type non sérialisable : {type(value)}")


class BacktestApp:
    def __init__(self, results_path=RESULTS, paper_dir=None):
        self.results_path = results_path
        self.paper = PaperManager(paper_dir or results_path.parent / "paper_trading" / "states")
        self.lock = Lock()
        self.running = False
        self.state = {"config": DEFAULT_CONFIG.copy(), "dataset": None,
                      "updated_at": None, "strategies": {}}
        if results_path.exists():
            self.state = json.loads(results_path.read_text())
        # Une exécution interrompue ne reste pas affichée en cours au redémarrage.
        for row in self.state["strategies"].values():
            if row["status"] in {"running", "pending"}:
                row.update(status="error", error="Exécution interrompue ; relancer les backtests.")

    def snapshot(self):
        with self.lock:
            state = deepcopy(self.state)
            for key, spec in get_strategies().items():
                state["strategies"].setdefault(key, {
                    "name": spec["name"], "description": spec["description"], "status": "idle",
                })
            policy = load_policy()
            specs = get_strategies()
            for key, row in state["strategies"].items():
                row["eligibility"] = eligibility_for(row, specs.get(key), policy)
            return {**state, "running": self.running, "policy": policy}

    def start_paper(self, key, manual=False):
        snapshot = self.snapshot()
        row = snapshot["strategies"].get(key)
        if snapshot["running"] or not row:
            raise ValueError("Attendre la fin du backtest avant de démarrer le paper.")
        approval = deepcopy(row["eligibility"])
        if approval["status"] == "not_tested":
            raise ValueError("Relancer les backtests pour tester le code actuel avant le paper.")
        if approval["status"] != "eligible" and not manual:
            raise ValueError("Stratégie non éligible : consulter les critères dans More.")
        approval["manual"] = manual
        if manual:
            approval["manual_at"] = datetime.now(timezone.utc).isoformat()
        config = deepcopy(snapshot["config"])
        self.paper.start(key, get_strategies()[key], config, approval)

    def start(self, options):
        config = DEFAULT_CONFIG.copy()
        for key in ("capital", "position_size", "fee_rate", "slippage_rate"):
            if key in options:
                config[key] = float(options[key])
        if not 0 < config["capital"] <= 1e9:
            raise ValueError("Capital attendu entre 0 et 1 milliard.")
        if not 0 < config["position_size"] <= 1:
            raise ValueError("Taille de position attendue entre 0 et 100 %.")
        if any(not 0 <= config[key] < 1 for key in ("fee_rate", "slippage_rate")):
            raise ValueError("Frais et slippage attendus entre 0 et 100 % exclus.")
        specs = get_strategies()
        with self.lock:
            if self.running:
                return False
            self.running = True
            self.state = {"config": config, "dataset": None, "updated_at": None,
                          "strategies": {key: {"name": spec["name"], "description": spec["description"],
                                               "status": "pending"} for key, spec in specs.items()}}
        Thread(target=self.run, args=(specs, config, bool(options.get("refresh", False)), bool(options.get("repair", False))), daemon=True).start()
        return True

    def persist(self):
        with self.lock:
            self.state["updated_at"] = datetime.now(timezone.utc).isoformat()
            text = json.dumps(self.state, default=json_default, allow_nan=False)
        temporary = self.results_path.with_suffix(".tmp")
        temporary.write_text(text)
        temporary.replace(self.results_path)

    def run(self, specs, config, refresh, repair=False):
        try:
            if repair:
                data = repair_history(config["symbol"], config["interval"], refresh=refresh)
            else:
                loader = get_ohlcv if refresh else load_ohlcv
                data = loader(config["symbol"], config["interval"])
            config["market_rules"] = get_pair_rules(config["symbol"], refresh=refresh)
            seconds = config["interval"] * 60
            gaps = data["timestamp"].diff().dt.total_seconds().dropna()
            with self.lock:
                self.state["dataset"] = {
                    "start": data.iloc[0]["timestamp"], "end": data.iloc[-1]["timestamp"],
                    "candles": len(data), "gaps": int((gaps > seconds).sum()),
                    "missing_candles": int(((gaps // seconds - 1).clip(lower=0)).sum()),
                }
            policy = load_policy()
            for key, spec in specs.items():
                with self.lock:
                    self.state["strategies"][key]["status"] = "running"
                try:
                    result = run_strategy(spec, data, config)
                    validation = validate_strategy(spec, data, config, result, policy)
                    with self.lock:
                        self.state["strategies"][key].update(status="done", result=result, validation=validation)
                except Exception as error:
                    with self.lock:
                        self.state["strategies"][key].update(status="error", error=str(error))
            self.persist()
        except Exception as error:
            with self.lock:
                for row in self.state["strategies"].values():
                    row.update(status="error", error=str(error))
        finally:
            with self.lock:
                self.running = False


def handler_for(app):
    class Handler(BaseHTTPRequestHandler):
        def send(self, status, payload, content_type="application/json"):
            body = (json.dumps(payload, default=json_default, allow_nan=False).encode()
                    if content_type == "application/json" else payload)
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/api/results":
                self.send(200, app.snapshot())
                return
            if path == "/api/paper":
                self.send(200, app.paper.snapshot())
                return
            files = {
                "/": ("index.html", "text/html; charset=utf-8"),
                "/index.html": ("index.html", "text/html; charset=utf-8"),
                "/paper": ("paper.html", "text/html; charset=utf-8"),
                "/paper.js": ("paper.js", "text/javascript; charset=utf-8"),
                "/strategy": ("strategy.html", "text/html; charset=utf-8"),
                "/style.css": ("style.css", "text/css; charset=utf-8"),
                "/common.js": ("common.js", "text/javascript; charset=utf-8"),
                "/index.js": ("index.js", "text/javascript; charset=utf-8"),
                "/strategy.js": ("strategy.js", "text/javascript; charset=utf-8"),
            }
            if path in files:
                filename, content_type = files[path]
                self.send(200, (ROOT / filename).read_bytes(), content_type)
            else:
                self.send(404, {"error": "Page introuvable"})

        def do_POST(self):
            # Évite qu'un autre site lance des calculs dans le serveur local.
            origin = self.headers.get("Origin")
            if origin and origin != f"http://{self.headers.get('Host')}":
                self.send(403, {"error": "Origine refusée"})
                return
            if self.path not in {"/api/run", "/api/paper/start", "/api/paper/pause"}:
                self.send(404, {"error": "Route introuvable"})
                return
            try:
                size = int(self.headers.get("Content-Length", 0))
                if not 0 < size <= 4096:
                    raise ValueError("Requête invalide")
                options = json.loads(self.rfile.read(size))
                if not isinstance(options, dict):
                    raise ValueError("Configuration invalide")
                if self.path == "/api/paper/start":
                    app.start_paper(options.get("strategy"), manual=options.get("manual") is True)
                    self.send(200, {"started": True})
                    return
                if self.path == "/api/paper/pause":
                    app.paper.pause(options.get("strategy"))
                    self.send(200, {"paused": True})
                    return
                started = app.start(options)
                self.send(202 if started else 409, {"started": started})
            except (ValueError, TypeError) as error:
                self.send(400, {"error": str(error)})

        def log_message(self, *args):
            pass
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    app = BacktestApp()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(app))
    app.paper.start_worker()
    print(f"Dashboard : http://127.0.0.1:{args.port} — arrêt avec Ctrl+C", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.paper.close()
        server.server_close()


if __name__ == "__main__":
    main()
