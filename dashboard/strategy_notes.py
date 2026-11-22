"""Documentation de présentation liée aux sources, sans exécution de stratégie."""
import inspect
import json
from pathlib import Path

NOTES = json.loads((Path(__file__).parent / 'strategy_notes.json').read_text())


def strategy_definition(key, spec):
    note = NOTES.get(key)
    verified = note is not None and note['source_hash'] == spec['source_hash']
    defaults = {name: p.default for name, p in list(inspect.signature(spec['function']).parameters.items())[1:]
                if p.default is not inspect.Parameter.empty and isinstance(p.default, (int, float, str, bool))}
    constants = {name: value for name, value in spec['function'].__globals__.items()
                 if name.isupper() and isinstance(value, (int, float, bool))}
    return {'constants': constants, 'description': spec['description'], 'warmup': spec['warmup'], 'defaults': defaults,
            'source_hash': spec['source_hash'], 'verified': verified,
            'notes': note if verified else None}
