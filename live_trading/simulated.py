"""Faux exchange persistant : aucun réseau, aucune clé API."""
from decimal import Decimal as D
import json
from pathlib import Path
import uuid


class Rejected(Exception):
    pass


class SimulatedKraken:
    real = False
    account_id = "simulated-account"

    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self.state = json.loads(self.path.read_text()) if self.path and self.path.exists() else {'eur': '1000', 'btc': '0', 'orders': {}}
        self.behavior = 'filled'
        self.price = D('50000')
        self.armed = False

    def arm(self, confirmation=None):
        self.armed = True

    def disarm(self):
        self.armed = False

    def save(self):
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps(self.state))
            temporary.replace(self.path)

    def balances(self):
        eur, btc = D(self.state['eur']), D(self.state['btc'])
        free_eur, free_btc = eur, btc
        for o in self.open_orders():
            remaining = D(o['quantity']) - sum((D(f['quantity']) for f in o['fills']), D(0))
            if o['side'] == 'buy':
                free_eur -= remaining * D(o['price']) * D('1.01')
            else:
                free_btc -= remaining
        return {'eur': eur, 'btc': btc, 'free_eur': free_eur, 'free_btc': free_btc}

    def quote(self, now):
        return {'bid': self.price, 'ask': self.price, 'time': now}

    def rules(self):
        return {'minimum': D('.00006'), 'costmin': D('.45'), 'step': D('.00000001'), 'tick': D('.1')}

    def open_orders(self):
        return [o for o in self.state['orders'].values() if o['status'] in ('open', 'partially_filled')]

    def find(self, client_id, txid=None):
        return self.state['orders'].get(client_id)

    def submit(self, order):
        if not self.armed:
            raise RuntimeError('Exécution désactivée.')
        if self.behavior == 'rejected':
            raise Rejected('Ordre refusé par le simulateur.')
        if order['id'] in self.state['orders']:
            raise RuntimeError('Doublon exchange.')
        o = dict(order, txid=str(uuid.uuid4()), status='open', fills=[])
        self.state['orders'][order['id']] = o
        if self.behavior in ('filled', 'partial', 'timeout'):
            self.fill(order['id'], D('.5') if self.behavior == 'partial' else D(1))
        if self.behavior == 'canceled':
            o['status'] = 'canceled'
        self.save()
        if self.behavior == 'timeout':
            raise TimeoutError('Réponse perdue après acceptation.')
        return o['txid']

    def fill(self, oid, fraction=D(1)):
        o = self.state['orders'][oid]
        done = sum((D(f['quantity']) for f in o['fills']), D(0))
        qty = (D(o['quantity']) - done) * fraction
        cost, fee = qty * self.price, qty * self.price * D('.004')
        sign = 1 if o['side'] == 'buy' else -1
        self.state['eur'] = str(D(self.state['eur']) - sign * cost - fee)
        self.state['btc'] = str(D(self.state['btc']) + sign * qty)
        o['fills'].append({'id': str(uuid.uuid4()), 'quantity': str(qty), 'cost': str(cost), 'fee': str(fee)})
        o['status'] = 'filled' if done + qty == D(o['quantity']) else 'partially_filled'
        self.save()

    def cancel(self, txid):
        for o in self.open_orders():
            if o['txid'] == txid:
                o['status'] = 'canceled'
        self.save()
