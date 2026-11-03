"""Une stratégie par module : strategy(history) renvoie 1, -1 ou 0."""
from importlib import import_module, reload
from hashlib import sha256
from pathlib import Path


def get_strategies():
    strategies = {}
    for path in sorted(Path(__file__).parent.glob("*.py")):
        if path.stem.startswith("_"):
            continue
        source_hash = sha256(path.read_bytes()).hexdigest()
        try:
            module = import_module(f"strategies.{path.stem}")
            if getattr(module, "_source_hash", source_hash) != source_hash:
                module = reload(module)
            module._source_hash = source_hash
        except Exception as error:
            message = f"Impossible de charger {path.stem} : {error}"
            def unavailable(history, message=message):
                raise ValueError(message)
            strategies[path.stem] = {"name": path.stem, "description": message, "error": message,
                                     "warmup": 1, "source_hash": source_hash, "function": unavailable}
            continue
        if callable(getattr(module, "strategy", None)):
            strategies[path.stem] = {
                "name": getattr(module, "NAME", path.stem),
                "description": getattr(module, "DESCRIPTION", ""),
                "warmup": getattr(module, "WARMUP", 1),
                "function": module.strategy,
                "source_hash": source_hash,
            }
    return strategies
