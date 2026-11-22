"""Projection en lecture seule des journaux live ; aucun import du moteur/client."""
import json
from contextlib import closing
from pathlib import Path
import sqlite3


def live_snapshot(directory):
    directory = Path(directory)
    result = {'journals': [], 'errors': [], 'complete': True, 'execution_status_observable': False}
    if not directory.exists():
        return result
    try:
        paths = sorted(directory.iterdir())
    except OSError:
        result.update(complete=False, errors=[{'journal': directory.name, 'message': 'Dossier de journaux inaccessible.'}])
        return result
    for path in paths:
        if path.suffix not in {'.sqlite', '.sqlite3', '.db'} or not path.is_file():
            continue
        try:
            with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=.2)) as db:
                db.row_factory = sqlite3.Row
                db.execute('BEGIN')
                metadata = {row['key']: json.loads(row['value']) for row in db.execute('SELECT key,value FROM meta')}
                identity = metadata.get('identity')
                if (not isinstance(identity, list) or len(identity) != 6
                        or type(identity[3]) is not bool or not isinstance(identity[0], str)):
                    raise ValueError('Identité non reconnue')
                if not identity[3]:
                    continue  # Une démo dry-run ne devient jamais une stratégie live.
                orders = [dict(row) for row in db.execute(
                    'SELECT id,side,quantity,price,status FROM orders ORDER BY rowid')]
                fills = [dict(row) for row in db.execute(
                    'SELECT order_id,quantity,cost,fee FROM fills ORDER BY rowid')]
                # Ne pas publier les IDs client/exchange ni le fingerprint du compte.
                for index, order in enumerate(orders):
                    order['fills'] = [dict(f) for f in fills if f['order_id'] == order['id']]
                    for fill in order['fills']:
                        fill.pop('order_id', None)
                    order.pop('id')
                    order['number'] = index + 1
                result['journals'].append({'journal': path.name, 'strategy': identity[0],
                    'execution_status': None, 'strategy_version': identity[1], 'orders': orders, 'fill_count': len(fills),
                    'last_candle': metadata.get('last_candle')})
        except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
            result['complete'] = False
            result['errors'].append({'journal': path.name, 'message': 'Journal illisible ou format non reconnu.'})
    return result
