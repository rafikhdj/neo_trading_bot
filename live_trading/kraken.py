"""Client d'exécution distinct ; aucun chargement des clés read-only."""
import os
import hashlib
import time
from decimal import Decimal as D
from urllib.parse import urlencode

import requests
from data.kraken_private import (KrakenPrivateClient, KrakenPrivateError,
    _REQUEST_LOCK, next_nonce, sign_request, _api_error)

CONFIRMATION = 'ENABLE KRAKEN BTC/EUR'


class KrakenExecutionClient:
    real = True

    def __init__(self, key, secret):
        self.auth = KrakenPrivateClient(key, secret)
        self.armed = False
        self.account_id = hashlib.sha256(key.encode()).hexdigest()

    @classmethod
    def from_env(cls):
        return cls(os.environ.get('KRAKEN_LIVE_API_KEY', ''), os.environ.get('KRAKEN_LIVE_API_SECRET', ''))

    def arm(self, confirmation=None):
        if confirmation != CONFIRMATION:
            raise RuntimeError('Confirmation live explicite requise.')
        self.armed = True

    def disarm(self):
        self.armed = False

    def _call(self, endpoint, params=None):
        allowed = {'BalanceEx', 'OpenOrders', 'ClosedOrders', 'QueryOrders', 'QueryTrades', 'AddOrder', 'CancelOrder'}
        if endpoint not in allowed or (endpoint in {'AddOrder', 'CancelOrder'} and not self.armed):
            raise RuntimeError('Endpoint interdit ou exécution désactivée.')
        with _REQUEST_LOCK:
            data = dict(params or {}, nonce=next_nonce())
            path = '/0/private/' + endpoint
            signature = sign_request(path, data, self.auth._api_secret)
            try:
                r = self.auth._session.post('https://api.kraken.com' + path, data=urlencode(data),
                    headers={'API-Key': self.auth._api_key, 'API-Sign': signature, 'Content-Type': 'application/x-www-form-urlencoded'}, timeout=15, allow_redirects=False)
                if r.status_code != 200:
                    raise KrakenPrivateError('HTTP Kraken indisponible ; état à réconcilier.')
                payload = r.json()
            except (requests.RequestException, ValueError):
                raise KrakenPrivateError('Réponse Kraken incertaine ; aucun réessai automatique.') from None
            if not isinstance(payload, dict) or not isinstance(payload.get('error'), list):
                raise KrakenPrivateError('Réponse Kraken invalide.')
            if payload['error']:
                # Même EOrder ne prouve pas l'absence d'un ordre côté exchange.
                # Le moteur conservera unknown ; aucun renvoi automatique.
                raise _api_error(payload['error'])
            if not isinstance(payload.get('result'), dict):
                raise KrakenPrivateError('Résultat Kraken invalide.')
            return self.auth._redact(payload['result'], signature)

    def balances(self):
        result = {'eur': D(0), 'btc': D(0), 'free_eur': D(0), 'free_btc': D(0)}
        for asset, row in self._call('BalanceEx').items():
            name = {'ZEUR': 'eur', 'EUR': 'eur', 'XXBT': 'btc', 'XBT': 'btc', 'BTC': 'btc'}.get(asset)
            balance = D(row['balance'])
            if D(row.get('credit', 0)) or D(row.get('credit_used', 0)):
                raise RuntimeError('Compte avec crédit incompatible avec ce MVP spot.')
            if not name:
                if balance:
                    raise RuntimeError('Compte dédié BTC/EUR requis.')
                continue
            result[name] += balance
            result['free_' + name] += balance - D(row.get('hold_trade', 0))
        return result

    def _public(self, endpoint):
        try:
            r = self.auth._session.get('https://api.kraken.com/0/public/' + endpoint,
                params={'pair': 'XBTEUR'}, timeout=15, allow_redirects=False)
            r.raise_for_status()
            payload = r.json()
            if payload['error']:
                raise ValueError()
            return next(iter(payload['result'].values()))
        except (requests.RequestException, ValueError, KeyError, StopIteration, TypeError):
            raise RuntimeError('Données publiques Kraken indisponibles.') from None

    def quote(self, now):
        start = time.time()
        row = self._public('Ticker')
        return {'bid': D(row['b'][0]), 'ask': D(row['a'][0]), 'time': start}

    def rules(self):
        row = self._public('AssetPairs')
        if row.get('status') != 'online':
            raise RuntimeError('Paire indisponible.')
        return {'minimum': D(row['ordermin']), 'costmin': D(row['costmin']),
                'step': D(10) ** -int(row['lot_decimals']), 'tick': D(row['tick_size'])}

    def _snapshot(self, txid, row):
        try:
            return self._parse_snapshot(txid, row)
        except (KeyError, TypeError, ValueError, ArithmeticError):
            raise RuntimeError('Schéma Kraken incomplet : réconciliation bloquée.') from None

    def _parse_snapshot(self, txid, row):
        if row['descr']['ordertype'] != 'limit':
            raise RuntimeError('Type d’ordre incompatible avec ce moteur limite IOC.')
        limit = D(row['descr']['price'])
        if not limit.is_finite() or limit <= 0:
            raise RuntimeError('Prix limite Kraken absent ou invalide.')
        if row['descr']['pair'] not in ('XBTEUR', 'XXBTZEUR', 'XBT/EUR', 'BTC/EUR') or row['descr'].get('leverage', 'none') not in ('none', '0'):
            raise RuntimeError('Ordre incompatible avec le périmètre spot BTC/EUR.')
        fills = []
        ids = row.get('trades', [])
        if not isinstance(ids, list) or any(not isinstance(i, str) for i in ids) or len(ids) != len(set(ids)):
            raise RuntimeError('Liste des exécutions invalide.')
        for offset in range(0, len(ids), 20):
            records = self._call('QueryTrades', {'txid': ','.join(ids[offset:offset + 20])})
            if set(records) != set(ids[offset:offset + 20]):
                raise RuntimeError('Exécutions demandées incomplètes ou supplémentaires.')
            for fid, f in records.items():
                if f['ordertxid'] != txid:
                    raise RuntimeError('Exécution attribuée à un autre ordre.')
                fills.append({'id': fid, 'quantity': f['vol'], 'cost': f['cost'], 'fee': f['fee']})
        if sum((D(f['quantity']) for f in fills), D(0)) != D(row['vol_exec']):
            raise RuntimeError('Exécutions Kraken incomplètes.')
        for field in ('cost', 'fee'):
            if abs(sum((D(f[field]) for f in fills), D(0)) - D(row[field])) > D('.000001'):
                raise RuntimeError('Totaux des exécutions incohérents.')
        status = {'pending': 'open', 'open': 'open', 'closed': 'filled', 'canceled': 'canceled', 'expired': 'canceled'}.get(row['status'])
        if status is None:
            raise RuntimeError('Statut Kraken inconnu.')
        if status == 'open' and D(row['vol_exec']):
            status = 'partially_filled'
        return {'id': row.get('cl_ord_id'), 'txid': txid, 'side': row['descr']['type'],
                'quantity': row['vol'], 'price': row['descr']['price'], 'status': status, 'fills': fills}

    def open_orders(self):
        return [self._snapshot(k, v) for k, v in self._call('OpenOrders', {'trades': 'true'})['open'].items()]

    def find(self, client_id, txid=None):
        try:
            return self._find(client_id, txid)
        except (KeyError, TypeError, ValueError, ArithmeticError):
            raise RuntimeError('Schéma de recherche Kraken incomplet ; aucun renvoi.') from None

    def _find(self, client_id, txid=None):
        if txid:
            rows = self._call('QueryOrders', {'txid': txid, 'trades': 'true'})
            if txid not in rows:
                return None
            snapshot = self._snapshot(txid, rows[txid])
            if snapshot['id'] not in (None, client_id):
                raise RuntimeError('Identifiant client incompatible avec le txid enregistré.')
            # Association fondée exclusivement sur le txid déjà durable, jamais le montant.
            snapshot['id'] = client_id
            return snapshot
        for row in self.open_orders():
            if row['id'] == client_id:
                return row
        # Le curseur évite les décalages d'offset pendant les clôtures concurrentes.
        cursor, seen, matches = None, set(), []
        for _ in range(1000):
            params = {'trades': 'true', 'with_cursor': 'true', 'cl_ord_id': client_id}
            if cursor:
                params['cursor'] = cursor
            result = self._call('ClosedOrders', params)
            for key, row in result['closed'].items():
                if not row.get('cl_ord_id'):
                    # Réponse filtrée : sans ID, impossible de prouver que cette ligne
                    # est sans rapport ou de garantir une association unique.
                    raise RuntimeError('Identifiant client absent sans txid durable : résolution manuelle requise.')
                if row.get('cl_ord_id') == client_id:
                    matches.append((key, row))
            page_cursor = result.get('cursor')
            if page_cursor is not None and (not isinstance(page_cursor, dict) or 'next' not in page_cursor):
                raise RuntimeError('Curseur Kraken malformé.')
            cursor = page_cursor['next'] if page_cursor else None
            if cursor is not None and (not isinstance(cursor, str) or not cursor):
                raise RuntimeError('Curseur Kraken invalide.')
            if not cursor:
                if len(matches) > 1:
                    raise RuntimeError('Plusieurs ordres pour une intention ; intervention requise.')
                return self._snapshot(*matches[0]) if matches else None
            if cursor in seen:
                break
            seen.add(cursor)
        raise RuntimeError('Historique incomplet ; réconciliation bloquée.')

    def submit(self, order):
        if order['side'] not in ('buy', 'sell') or any(not D(order[k]).is_finite() or D(order[k]) <= 0 for k in ('quantity', 'price')):
            raise ValueError('Ordre spot invalide.')
        result = self._call('AddOrder', {'pair': 'XBTEUR', 'type': order['side'], 'ordertype': 'limit',
            'volume': order['quantity'], 'price': order['price'], 'timeinforce': 'IOC',
            'oflags': 'fciq', 'cl_ord_id': order['id']})
        if len(result.get('txid', [])) != 1:
            raise RuntimeError('Acceptation incertaine.')
        return result['txid'][0]

    def cancel(self, txid):
        self._call('CancelOrder', {'txid': txid})
