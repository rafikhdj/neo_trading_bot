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
    return {'descr': {'pair': 'XBTEUR', 'type': 'buy', 'price': '50100', 'leverage': 'none'},
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
