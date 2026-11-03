"""Filtre explicite pour autoriser une stratégie en paper, sans optimisation."""
import json
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent / "validation_config.json"


def load_policy(path=CONFIG_PATH):
    policy = json.loads(path.read_text())
    if not 0 < policy["validation_fraction"] < 1:
        raise ValueError("validation_fraction doit être entre 0 et 1 exclus.")
    if not 0 <= policy["max_drawdown"] <= 1 or policy["min_return"] < 0:
        raise ValueError("Drawdown attendu entre 0 et 1 ; rendement minimum positif ou nul.")
    if any(not isinstance(policy[k], int) or policy[k] < 1
           for k in ("min_closed_trades", "min_validation_trades")):
        raise ValueError("Les minimums de trades doivent être des entiers positifs.")
    return policy


def assess(full, validation, policy):
    checks = [
        {"label": "Trades clôturés au total", "passed": full["closed_trades"] >= policy["min_closed_trades"],
         "actual": full["closed_trades"], "required": policy["min_closed_trades"], "kind": "count"},
        {"label": "Trades clôturés en validation", "passed": validation["closed_trades"] >= policy["min_validation_trades"],
         "actual": validation["closed_trades"], "required": policy["min_validation_trades"], "kind": "count"},
    ]
    for name, summary in (("Total", full), ("Validation", validation)):
        checks.extend([
            {"label": f"{name} · rendement net", "passed": summary["return"] > policy["min_return"],
             "actual": summary["return"], "required": policy["min_return"], "kind": "return"},
            {"label": f"{name} · drawdown maximum", "passed": summary["max_drawdown"] <= policy["max_drawdown"],
             "actual": summary["max_drawdown"], "required": policy["max_drawdown"], "kind": "drawdown"},
        ])
    status = ("insufficient" if not all(c["passed"] for c in checks[:2])
              else "eligible" if all(c["passed"] for c in checks) else "rejected")
    return {"status": status, "checks": checks}


def eligibility_for(row, spec, policy):
    validation = row.get("validation")
    if (row.get("status") == "done" and validation and spec and not spec.get("error")
            and validation["source_hash"] == spec["source_hash"]
            and validation["policy"]["validation_fraction"] == policy["validation_fraction"]):
        return {**assess(row["result"]["summary"], validation["summary"], policy),
                "source_hash": validation["source_hash"], "policy": policy}
    return {"status": "not_tested", "checks": [],
            "reason": "Relancer les backtests pour évaluer cette version et sa validation."}
