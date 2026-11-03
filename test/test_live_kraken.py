"""Transport réel entièrement remplacé par des réponses HTTP en mémoire."""
from decimal import Decimal as D
from unittest.mock import Mock
from urllib.parse import parse_qs

import pytest
import requests

from data.kraken_private import sign_request, KrakenPrivateError
from live_trading.kraken import KrakenExecutionClient, CONFIRMATION
from live_trading.simulated import Rejected


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(requests.sessions.Session, 'request', Mock(side_effect=AssertionError('Réseau interdit')))
    c = KrakenExecutionClient('testApiKey', 'c2VjcmV0')
    yield c
    c.auth.close()


def response(result=None, errors=None, status=200):
    return Mock(status_code=status, json=Mock(return_value={'error': errors or [], 'result': result or {}}))


def row(status='closed', executed='.0001'):
    return {'descr': {'pair': 'XBTEUR', 'type': 'buy', 'ordertype': 'limit', 'price': '50100', 'leverage': 'none'},
            'cl_ord_id': 'client-1', 'vol': '.0001', 'vol_exec': executed, 'cost': '5' if D(executed) else '0',
            'fee': '.02' if D(executed) else '0', 'status': status, 'trades': ['trade-1'] if D(executed) else []}


def test_signed_submit_acceptance_not_fill(client):
    client.auth._session.post = Mock(return_value=response({'txid': ['order-1']}))
    client.arm(CONFIRMATION)
    assert client.submit({'id': 'client-1', 'side': 'buy', 'quantity': '.0001', 'price': '50100'}) == 'order-1'
    args, kwargs = client.auth._session.post.call_args
    data = {k: v[0] for k, v in parse_qs(kwargs['data']).items()}
    assert data['pair'] == 'XBTEUR' and data['timeinforce'] == 'IOC' and data['oflags'] == 'fciq'
    assert 'leverage' not in data
    assert kwargs['headers']['API-Sign'] == sign_request('/0/private/AddOrder', data, 'c2VjcmV0')
    assert kwargs['allow_redirects'] is False


def test_query_confirmed_fills(client):
    client.auth._session.post = Mock(side_effect=[response({'order-1': row()}), response({
        'trade-1': {'ordertxid': 'order-1', 'vol': '.0001', 'cost': '5', 'fee': '.02'}})])
    result = client.find('client-1', 'order-1')
    assert result['status'] == 'filled' and result['fills'][0]['fee'] == '.02'


def test_accepted_open_without_fills(client):
    client.auth._session.post = Mock(return_value=response({'order-1': row('open', '0')}))
    result = client.find('client-1', 'order-1')
    assert result['status'] == 'open' and result['fills'] == []


def test_missing_fills_block(client):
    client.auth._session.post = Mock(side_effect=[response({'order-1': row()}), response({})])
    with pytest.raises(RuntimeError):
        client.find('client-1', 'order-1')


def test_paginated_recovery(client):
    client.auth._session.post = Mock(side_effect=[response({'open': {}}),
        response({'closed': {'unrelated': {'cl_ord_id': 'other'}}, 'count': 2, 'cursor': {'next': 'next-page'}}),
        response({'closed': {'order-1': row('canceled', '0')}, 'count': 2, 'cursor': {'next': None}})])
    assert client.find('client-1')['status'] == 'canceled'
    assert 'cursor=next-page' in client.auth._session.post.call_args.kwargs['data']


def test_balance_reservations(client):
    client.auth._session.post = Mock(return_value=response({'ZEUR': {'balance': '100', 'hold_trade': '20'},
        'XXBT': {'balance': '.1', 'hold_trade': '.01'}}))
    assert client.balances() == {'eur': D(100), 'free_eur': D(80), 'btc': D('.1'), 'free_btc': D('.09')}


@pytest.mark.parametrize('failure', ['timeout','http','auth','rate','malformed','rejected'])
def test_errors_sanitized_no_retry(client, failure):
    client.arm(CONFIRMATION)
    secret = 'testApiKey c2VjcmV0'
    r = response()
    if failure == 'timeout':
        client.auth._session.post = Mock(side_effect=requests.Timeout(secret))
    else:
        if failure == 'http':
            r = response(status=503)
        if failure in ('auth', 'rate', 'rejected'):
            error = {'auth':'EAPI:Invalid key', 'rate':'EAPI:Rate limit exceeded', 'rejected':'EOrder:Insufficient funds'}[failure]
            r = response(errors=[error + secret])
        if failure == 'malformed':
            r.json.return_value = {'secret': secret}
        client.auth._session.post = Mock(return_value=r)
    with pytest.raises((KrakenPrivateError, Rejected)) as exc:
        client.submit({'id': 'client-1', 'side': 'buy', 'quantity': '.0001', 'price': '50100'})
    assert 'testApiKey' not in str(exc.value) and 'c2VjcmV0' not in str(exc.value)
    assert client.auth._session.post.call_count == 1


def test_controlled_cancel(client):
    client.auth._session.post = Mock(return_value=response({'count': 1}))
    client.arm(CONFIRMATION)
    client.cancel('order-1')
    assert client.auth._session.post.call_args.args[0].endswith('/CancelOrder')
    assert 'txid=order-1' in client.auth._session.post.call_args.kwargs['data']
    client.disarm()
    with pytest.raises(RuntimeError):
        client.cancel('order-1')


@pytest.fixture
def closed_fixture():
    import json
    from pathlib import Path
    return json.loads((Path(__file__).parent / 'fixtures/kraken/query_closed_without_client_id.json').read_text())


def test_known_txid_without_client_id_uses_durable_binding(client, closed_fixture):
    client.auth._session.post = Mock(side_effect=[Mock(status_code=200, json=Mock(return_value=closed_fixture)),
        response({'TEXAMPLE-TRADE-000001': {'ordertxid': 'OEXAMPLE-ORDER-000001', 'vol': '.0001', 'cost': '5', 'fee': '.02'}})])
    result = client.find('durable-client-id', 'OEXAMPLE-ORDER-000001')
    assert result['id'] == 'durable-client-id'
    assert result['price'] == '50100.0'  # Pas le prix moyen 50000 à la racine.
    assert result['status'] == 'filled'


def test_known_txid_conflicting_client_id_blocks(client):
    client.auth._session.post = Mock(return_value=response({'order-1': row('canceled', '0')}))
    with pytest.raises(RuntimeError, match='Identifiant client incompatible'):
        client.find('different-client', 'order-1')


@pytest.mark.parametrize('cursor', ['absent', None, {'next': None}])
def test_terminal_page_cursor_optional(client, cursor):
    page = {'closed': {}, 'count': 0}
    if cursor != 'absent':
        page['cursor'] = cursor
    client.auth._session.post = Mock(side_effect=[response({'open': {}}), response(page)])
    assert client.find('lost-client-id') is None
    assert client.auth._session.post.call_count == 2


@pytest.mark.parametrize('cursor', [{}, {'next': 42}, {'next': ''}, 'bad'])
def test_malformed_cursor_blocks(client, cursor):
    client.auth._session.post = Mock(side_effect=[response({'open': {}}), response({'closed': {}, 'cursor': cursor})])
    with pytest.raises(RuntimeError, match='Curseur'):
        client.find('lost-client-id')


def test_no_client_id_no_durable_txid_never_matches_amount(client):
    r = row('canceled', '0')
    del r['cl_ord_id']
    client.auth._session.post = Mock(side_effect=[response({'open': {}}), response({'closed': {'same-amount': r}})])
    with pytest.raises(RuntimeError, match='sans txid durable'):
        client.find('lost-client-id')


@pytest.mark.parametrize('missing', ['descr', 'price', 'trades'])
def test_missing_required_snapshot_data_blocks(client, missing):
    r = row()
    if missing == 'price':
        del r['descr']['price']
    else:
        del r[missing]
    client.auth._session.post = Mock(return_value=response({'order-1': r}))
    with pytest.raises(RuntimeError):
        client.find('client-1', 'order-1')


@pytest.mark.parametrize('error', ['EOrder:Insufficient funds', 'EOrder:Unknown order', 'EOrder:Unknown error', 'EOrder:Rate limit exceeded'])
def test_all_addorder_api_errors_remain_uncertain(client, error):
    client.arm(CONFIRMATION)
    client.auth._session.post = Mock(return_value=response(errors=[error]))
    with pytest.raises(KrakenPrivateError) as exc:
        client.submit(dict(id='client-1', side='buy', quantity='.0001', price='50100'))
    assert not isinstance(exc.value, Rejected)
    assert client.auth._session.post.call_count == 1


@pytest.mark.parametrize('field,value', [('cost', '6'), ('fee', '.03'), ('vol', '.0002'), ('ordertxid', 'different')])
def test_inconsistent_trade_totals_block(client, field, value):
    trade = {'ordertxid': 'order-1', 'vol': '.0001', 'cost': '5', 'fee': '.02'}
    trade[field] = value
    client.auth._session.post = Mock(side_effect=[response({'order-1': row()}), response({'trade-1': trade})])
    with pytest.raises(RuntimeError):
        client.find('client-1', 'order-1')


def test_engine_reconciles_known_txid_with_missing_client_id(client, closed_fixture, tmp_path):
    from live_trading.engine import Engine
    from test.test_live_trading import buy
    engine = Engine(tmp_path / 'known.sqlite', client, buy, 'known')
    try:
        engine.journal.put('baseline', {'eur': '1000', 'btc': '0'})
        order = dict(id='durable-client', candle='1', strategy='known', side='buy', quantity='.0001', price='50100')
        engine.journal.prepare(order)
        engine.journal.update(order['id'], 'unknown', txid='OEXAMPLE-ORDER-000001')
        client.balances = Mock(return_value={'eur': D('994.98'), 'btc': D('.0001'), 'free_eur': D('994.98'), 'free_btc': D('.0001')})
        # Lecture concurrente : l'ordre était encore ouvert, mais sans ID client.
        client.open_orders = Mock(return_value=[{'id': None, 'txid': 'OEXAMPLE-ORDER-000001'}])
        client.auth._session.post = Mock(side_effect=[Mock(status_code=200, json=Mock(return_value=closed_fixture)),
            response({'TEXAMPLE-TRADE-000001': {'ordertxid': 'OEXAMPLE-ORDER-000001', 'vol': '.0001', 'cost': '5', 'fee': '.02'}})])
        assert engine.reconcile()
        assert engine.journal.orders()[0]['status'] == 'filled'
        assert engine.portfolio()['btc'] == D('.0001')
        assert not engine.enabled and not client.armed
    finally:
        engine.close()
