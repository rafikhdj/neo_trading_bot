"""Une stratégie par module : strategy(history) renvoie 1, -1 ou 0."""
from importlib import import_module
from pathlib import Path


def get_strategies():
    strategies = {}
    for path in sorted(Path(__file__).parent.glob("*.py")):
        if path.stem.startswith("_"):
            continue
        module = import_module(f"strategies.{path.stem}")
        if callable(getattr(module, "strategy", None)):
            strategies[path.stem] = {
                "name": getattr(module, "NAME", path.stem),
                "description": getattr(module, "DESCRIPTION", ""),
                "warmup": getattr(module, "WARMUP", 1),
                "function": module.strategy,
            }
    return strategies
