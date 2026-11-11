"""Diagnostic Kraken sans ordres, activation, journal ni données de compte affichées.

python -m live_trading.verify_read_only [--public-only] [--max-pages 5]
"""
import argparse
from decimal import Decimal as D
import json
from pathlib import Path
import time
import uuid

from data.kraken_private import _credentials_from_env
from .kraken import KrakenExecutionClient

_READS = frozenset({'GetApiKeyInfo', 'BalanceEx', 'OpenOrders', 'ClosedOrders', 'QueryOrders', 'QueryTrades'})
_REQUIRED_PERMISSIONS = frozenset({'query-funds', 'query-open-trades', 'query-closed-trades'})
_ALLOWED_PERMISSIONS = _REQUIRED_PERMISSIONS | {'query-ledger'}


class PermissionCheckError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__('Permissions read-only non vérifiées.')


class ReadOnlyValidationClient(KrakenExecutionClient):
    """Transport et parsers live réutilisés ; aucune méthode d'écriture disponible."""
    def __init__(self, key, secret):
        super().__init__(key, secret)
        self.permissions_verified = False
        self.calls = []

    @classmethod
    def from_env(cls):
        # Jamais les variables KRAKEN_LIVE_* ; ne pas afficher le fichier ou ses valeurs.
        path = Path(__file__).resolve().parent.parent / '.env'
        return cls(*_credentials_from_env(path))

    def arm(self, confirmation=None):
        raise RuntimeError('Activation interdite dans ce diagnostic.')

    def submit(self, order):
        raise RuntimeError('Création interdite dans ce diagnostic.')

    def cancel(self, txid):
        raise RuntimeError('Annulation interdite dans ce diagnostic.')

    def _call(self, endpoint, params=None):
        if endpoint not in _READS or (endpoint != 'GetApiKeyInfo' and not self.permissions_verified):
            raise RuntimeError('Lecture interdite avant vérification des permissions.')
        entry = {'endpoint': endpoint, 'outcome': 'failed'}
        self.calls.append(entry)
        result = super()._call(endpoint, params)
        entry['outcome'] = 'ok'
        return result

    def _public(self, endpoint):
        if endpoint not in {'AssetPairs', 'Ticker'}:
            raise RuntimeError('Endpoint public non autorisé.')
        entry = {'endpoint': endpoint, 'outcome': 'failed'}
        self.calls.append(entry)
        result = super()._public(endpoint)
        entry['outcome'] = 'ok'
        return result

    def verify_permissions(self):
        self.permissions_verified = False
        info = self._call('GetApiKeyInfo')
        permissions = info.get('permissions')
        if not isinstance(permissions, list) or not all(isinstance(p, str) for p in permissions):
            raise PermissionCheckError('permissions_schema_unverified')
        if not set(permissions).issubset(_ALLOWED_PERMISSIONS):
            raise PermissionCheckError('non_read_only_or_unknown_permissions_present')
        if not _REQUIRED_PERMISSIONS.issubset(permissions):
            raise PermissionCheckError('required_read_permissions_missing')
        self.permissions_verified = True

    def closed_history(self, max_pages):
        """Sonde non filtrée : curseur complet ou échec, sans renvoyer l'historique au terminal."""
        result, cursor, seen = {}, None, set()
        for _ in range(max_pages):
            params = {'trades': 'true', 'with_cursor': 'true'}
            if cursor:
                params['cursor'] = cursor
            page = self._call('ClosedOrders', params)
            rows = page['closed']
            if not isinstance(rows, dict) or any(not isinstance(r, dict) for r in rows.values()):
                raise RuntimeError('Schéma ClosedOrders invalide.')
            if result.keys() & rows.keys():
                raise RuntimeError('Historique répété : résultat incomplet.')
            result.update(rows)
            metadata = page.get('cursor')
            if metadata is not None and (not isinstance(metadata, dict) or 'next' not in metadata):
                raise RuntimeError('Curseur invalide.')
            cursor = metadata.get('next') if metadata is not None else None
            if cursor is None:
                return result
            if not isinstance(cursor, str) or not cursor or cursor in seen:
                raise RuntimeError('Curseur invalide ou répété.')
            seen.add(cursor)
        raise RuntimeError('Plafond de pages atteint : vérification incomplète.')


def _check(report, name, action, detail):
    try:
        value = action()
        report['checks'][name] = {'status': 'ok', 'detail': detail(value)}
        return True, value
    except PermissionCheckError as error:
        report['checks'][name] = {'status': 'failed', 'detail': error.code}
        return False, None
    except Exception:
        # Jamais str(exception), réponse brute, nonce, signature, montant ou identifiant.
        report['checks'][name] = {'status': 'failed', 'detail': 'access_network_or_schema_unverified'}
        return False, None


def verify(client, public_only=False, max_pages=5):
    report = {'mode': 'read_only', 'checks': {}, 'calls': client.calls, 'result': 'incomplete'}
    def rules():
        values = client.rules()
        if any(not v.is_finite() or v <= 0 for v in values.values()):
            raise RuntimeError('Règles invalides.')
        return values
    def quote():
        value = client.quote(time.time())
        if not 0 < value['bid'] <= value['ask'] or not all(value[k].is_finite() for k in ('bid', 'ask')):
            raise RuntimeError('Cotation invalide.')
        if not 0 <= time.time() - value['time'] <= 30:
            raise RuntimeError('Réception trop ancienne.')
        return value
    _check(report, 'AssetPairs', rules, lambda _: 'live_parser_rules_valid')
    _check(report, 'Ticker', quote, lambda _: 'live_parser_quote_valid')
    if public_only:
        report['checks']['private'] = {'status': 'skipped', 'detail': 'public_only_requested'}
        return report
    ok, _ = _check(report, 'permissions', client.verify_permissions, lambda _: 'strict_read_only_verified')
    if not ok:
        return report
    def balances():
        value = client.balances()
        if any(not v.is_finite() or v < 0 for v in value.values()):
            raise RuntimeError('Soldes invalides.')
        return value
    _check(report, 'BalanceEx', balances, lambda _: 'live_parser_balances_valid_values_omitted')
    opened_ok, opened = _check(report, 'OpenOrders', client.open_orders,
                               lambda rows: 'live_parser_empty' if not rows else 'live_parser_nonempty_values_omitted')
    before_pages = sum(c['endpoint'] == 'ClosedOrders' for c in client.calls)
    history_ok, history = _check(report, 'ClosedOrders', lambda: client.closed_history(max_pages),
                                 lambda rows: 'empty_terminal_page' if not rows else 'history_complete_values_omitted')
    pages = sum(c['endpoint'] == 'ClosedOrders' for c in client.calls) - before_pages
    report['checks']['pagination_next'] = {'status': 'ok' if history_ok and pages > 1 else 'skipped',
                                          'detail': 'next_cursor_exercised' if history_ok and pages > 1 else 'no_complete_multi_page_history'}
    if not history_ok or not opened_ok:
        return report
    # Sonde négative sans création : ne jamais réutiliser cet UUID pour un ordre.
    probe_id = str(uuid.uuid4())
    def negative_filter():
        if client.find(probe_id) is not None:
            raise RuntimeError('Sonde négative non vide.')
    _check(report, 'ClosedOrders_filter_negative', negative_filter, lambda _: 'no_match_for_unsent_uuid')
    candidates = [(k, v) for k, v in history.items()]
    if not candidates:
        candidates = [(o['txid'], None) for o in opened]
    if not candidates:
        for name in ('QueryOrders', 'QueryTrades', 'ClosedOrders_filter_positive'):
            report['checks'][name] = {'status': 'skipped', 'detail': 'no_real_order_data'}
        return report
    txid, _ = candidates[0]
    def query():
        rows = client._call('QueryOrders', {'txid': txid, 'trades': 'true'})
        row = rows[txid]
        if not isinstance(row, dict):
            raise RuntimeError('Ordre absent.')
        return row
    ok, row = _check(report, 'QueryOrders', query, lambda _: 'real_order_returned_values_omitted')
    if not ok:
        return report
    ids = row.get('trades', [])
    if not isinstance(ids, list) or any(not isinstance(i, str) for i in ids) or len(ids) != len(set(ids)):
        report['checks']['QueryTrades'] = {'status': 'failed', 'detail': 'invalid_trade_list'}
        return report
    if ids:
        def trades():
            for offset in range(0, len(ids), 20):
                batch = ids[offset:offset + 20]
                records = client._call('QueryTrades', {'txid': ','.join(batch)})
                if set(records) != set(batch) or any(r['ordertxid'] != txid for r in records.values()):
                    raise RuntimeError('Exécutions incohérentes.')
                for r in records.values():
                    if any(not D(r[k]).is_finite() or D(r[k]) < 0 for k in ('vol', 'cost', 'fee')):
                        raise RuntimeError('Montants invalides.')
        _check(report, 'QueryTrades', trades, lambda _: 'real_trades_returned_values_omitted')
    else:
        report['checks']['QueryTrades'] = {'status': 'skipped', 'detail': 'no_trade_ids_available'}
    # Vérifier également le parseur exact utilisé par la réconciliation live.
    _check(report, 'order_snapshot', lambda: client._snapshot(txid, row), lambda _: 'live_parser_snapshot_valid')
    matching = next(((key, value) for key, value in history.items() if value.get('cl_ord_id')), None)
    if matching:
        key, value = matching
        def positive_filter():
            before = sum(c['endpoint'] == 'ClosedOrders' for c in client.calls)
            snapshot = client.find(value['cl_ord_id'])
            after = sum(c['endpoint'] == 'ClosedOrders' for c in client.calls)
            if after <= before or snapshot is None or snapshot['txid'] != key:
                raise RuntimeError('Filtre non confirmé.')
        _check(report, 'ClosedOrders_filter_positive', positive_filter, lambda _: 'real_client_id_match_verified')
    else:
        report['checks']['ClosedOrders_filter_positive'] = {'status': 'skipped', 'detail': 'no_real_closed_client_id'}
    if all(check['status'] == 'ok' for check in report['checks'].values()):
        report['result'] = 'checks_passed_not_live_authorization'
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--public-only', action='store_true')
    parser.add_argument('--max-pages', type=int, choices=range(1, 21), default=5, metavar='1..20')
    args = parser.parse_args(argv)
    client = None
    try:
        # En mode public, credentials synthétiques seulement ; aucune lecture .env.
        client = ReadOnlyValidationClient('publicOnly', 'cHVibGlj') if args.public_only else ReadOnlyValidationClient.from_env()
        report = verify(client, args.public_only, args.max_pages)
    except Exception:
        report = {'mode': 'read_only', 'result': 'incomplete', 'checks': {
            'credentials_or_setup': {'status': 'failed', 'detail': 'read_only_credentials_unavailable'}},
            'calls': [] if client is None else client.calls}
    finally:
        if client is not None:
            client.auth.close()
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if any(c['status'] == 'failed' for c in report['checks'].values()):
        return 1
    return 0 if args.public_only or report['result'] != 'incomplete' else 2


if __name__ == '__main__':
    raise SystemExit(main())
