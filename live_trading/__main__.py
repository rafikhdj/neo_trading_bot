"""CLI sans commande d'envoi réel. Démonstration intégralement locale."""
import argparse
from decimal import Decimal as D
import json
from pathlib import Path
import sqlite3
import sys
import time

import pandas as pd
from strategies.moving_average import strategy
from .engine import Engine
from data.kraken_private import KrakenPrivateError
from .simulated import SimulatedKraken


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['status', 'orders', 'reconcile', 'demo', 'kill'])
    parser.add_argument('--db', type=Path, default=Path('live_trading/state/dry_run.sqlite'))
    parser.add_argument('--kraken', action='store_true', help='Réconciliation réelle en lecture uniquement ; exige --db dédié.')
    args = parser.parse_args()
    if args.kraken and (args.command != 'reconcile' or args.db == Path('live_trading/state/dry_run.sqlite')):
        parser.error('--kraken exige reconcile et un --db distinct du dry-run.')
    if args.command == 'kill':
        args.db.parent.mkdir(parents=True, exist_ok=True)
        args.db.with_suffix('.kill').touch()
        print('Kill switch activé ; aucune liquidation.')
        return
    if args.command in ('status', 'orders'):
        if not args.db.exists():
            print('Journal absent. Exécution désactivée ; dry-run par défaut.')
            return
        with sqlite3.connect(args.db.resolve().as_uri() + '?mode=ro', uri=True) as db:
            db.row_factory = sqlite3.Row
            tables = ('orders', 'fills') if args.command == 'orders' else ('meta',)
            print(json.dumps({t: [dict(r) for r in db.execute('SELECT * FROM ' + t)] for t in tables}, indent=2))
        print('Cette commande de lecture ne démarre ni n’active un moteur.')
        return
    if args.kraken:
        from .kraken import KrakenExecutionClient
        broker = KrakenExecutionClient.from_env()
    else:
        broker = SimulatedKraken(str(args.db) + '.exchange.json')
    engine = Engine(args.db, broker, strategy, 'moving_average')
    try:
        if args.command == 'reconcile':
            ok = engine.reconcile()
            print('Réconciliation (Kraken lecture seule)' if args.kraken else 'Réconciliation dry-run', ok, '| désactivé :', not engine.enabled)
            return 0 if ok else 1
        if engine.journal.orders():
            print('Démo déjà enregistrée ; choisir un nouveau --db pour un nouveau cycle.')
            return
        end = int(time.time() // 300) * 300
        stamps = pd.date_range(end=pd.Timestamp(end, unit='s', tz='UTC') - pd.Timedelta(minutes=5), periods=60, freq='5min')
        prices = [50000 + i * 10 for i in range(30)] + [50300 - i * 30 for i in range(30)]
        data = pd.DataFrame({'timestamp': stamps, 'close': prices})
        for column in ('open', 'high', 'low'):
            data[column] = data['close']
        data['volume'] = 1
        activation = stamps[29].timestamp() + 299
        broker.price = D(str(prices[29]))
        if not engine.activate(now=activation):
            raise RuntimeError(engine.reason)
        for index in range(29, 60):
            broker.price = D(str(prices[index]))
            engine.cycle(data.iloc[:index + 1], now=stamps[index].timestamp() + 300)
        print(json.dumps({'mode': 'dry-run', 'orders': engine.journal.orders(), 'fills': engine.journal.fills(),
                          'portfolio': {k: str(v) for k, v in engine.portfolio().items()}}, indent=2))
    finally:
        engine.close()


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, RuntimeError, ValueError, sqlite3.Error, KrakenPrivateError):
        print('Commande bloquée : vérifier le journal, la configuration et les contrôles ; aucune activation automatique.', file=sys.stderr)
        sys.exit(1)
