"""Journal durable mono-processus. Les montants sont des décimaux texte."""
import fcntl
import json
from pathlib import Path
import sqlite3


class Journal:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = open(str(path) + '.lock', 'a')
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock.close()
            raise RuntimeError('Journal déjà utilisé par un autre moteur.') from None
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS orders (
          id TEXT PRIMARY KEY, candle TEXT UNIQUE NOT NULL, strategy TEXT NOT NULL,
          side TEXT NOT NULL, quantity TEXT NOT NULL, price TEXT NOT NULL,
          status TEXT NOT NULL, txid TEXT UNIQUE, reason TEXT);
        CREATE TABLE IF NOT EXISTS fills (
          id TEXT PRIMARY KEY, order_id TEXT NOT NULL, quantity TEXT NOT NULL,
          cost TEXT NOT NULL, fee TEXT NOT NULL);
        ''')

    def get(self, key, default=None):
        row = self.db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, key, value):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (key, json.dumps(value)))

    def orders(self):
        return [dict(r) for r in self.db.execute('SELECT * FROM orders ORDER BY rowid')]

    def fills(self):
        return [dict(r) for r in self.db.execute('SELECT * FROM fills ORDER BY rowid')]

    def prepare(self, order):
        with self.db:
            self.db.execute('INSERT INTO orders(id,candle,strategy,side,quantity,price,status) VALUES (:id,:candle,:strategy,:side,:quantity,:price,\'prepared\')', order)

    def update(self, oid, status, txid=None, reason=None):
        with self.db:
            self.db.execute('UPDATE orders SET status=?,txid=COALESCE(?,txid),reason=? WHERE id=?', (status, txid, reason, oid))

    def confirm(self, order, snapshot):
        with self.db:
            for fill in snapshot['fills']:
                values = (fill['id'], order['id'], fill['quantity'], fill['cost'], fill['fee'])
                old = self.db.execute('SELECT * FROM fills WHERE id=?', (fill['id'],)).fetchone()
                if old and tuple(old) != values:
                    raise RuntimeError('Exécution modifiée ou attribuée à deux ordres.')
                self.db.execute('INSERT OR IGNORE INTO fills VALUES (?,?,?,?,?)', values)
            self.db.execute('UPDATE orders SET status=?,txid=? WHERE id=?', (snapshot['status'], snapshot['txid'], order['id']))

    def close(self):
        self.db.close()
        self.lock.close()
