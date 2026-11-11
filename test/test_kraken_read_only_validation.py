"""Validation du diagnostic sans réseau, fichier de compte, armement ou journal."""
from decimal import Decimal as D
import json
from unittest.mock import Mock
from urllib.parse import parse_qs

import pytest
import requests

from live_trading import verify_read_only as diagnostic
from test.test_live_kraken import response, row

READ_PERMISSIONS = ['query-funds', 'query-open-trades', 'query-closed-trades']


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(requests.sessions.Session, 'request', Mock(side_effect=AssertionError('Réseau interdit')))
    c = diagnostic.ReadOnlyValidationClient('fakeKey', 'c2VjcmV0')
    c.auth._session.post = Mock(return_value=response({'permissions': READ_PERMISSIONS}))
    c.auth._session.get = Mock()
    yield c
    c.auth.close()


def public(c):
    c.rules = Mock(return_value={'minimum': D('.00006'), 'costmin': D('.45'), 'step': D('.00000001'), 'tick': D('.1')})
    c.quote = lambda now: {'bid': D(50000), 'ask': D(50001), 'time': now}


def test_transport_refuses_all_writes_before_network(client):
    for endpoint in ('AddOrder', 'CancelOrder', 'Withdraw', 'AmendOrder', 'CancelAll', 'BalanceEx'):
        with pytest.raises(RuntimeError):
            client._call(endpoint)
    client.auth._session.post.assert_not_called()
    assert not client.armed


@pytest.mark.parametrize('permissions', [[], None, 'query-funds', ['modify-trades'], READ_PERMISSIONS + ['close-trades'], READ_PERMISSIONS + ['unknown'], READ_PERMISSIONS + ['withdraw-funds']])
def test_permissions_fail_closed(client, permissions):
    client.auth._session.post.return_value = response({'permissions': permissions})
    with pytest.raises(RuntimeError):
        client.verify_permissions()
    assert not client.permissions_verified
    with pytest.raises(RuntimeError):
        client._call('BalanceEx')
    assert client.auth._session.post.call_count == 1


def test_verified_permissions_never_enable_write_transport(client):
    client.verify_permissions()
    assert client.permissions_verified and not client.armed
    with pytest.raises(RuntimeError):
        client._call('AddOrder')
    assert client.auth._session.post.call_count == 1


def test_bad_permissions_stop_workflow_before_account_reads(client):
    public(client)
    client.auth._session.post.return_value = response({'permissions': READ_PERMISSIONS + ['modify-trades']})
    report = diagnostic.verify(client)
    assert report['checks']['permissions']['status'] == 'failed'
    assert [call['endpoint'] for call in report['calls']] == ['GetApiKeyInfo']
    assert report['result'] == 'incomplete'


def test_empty_account_and_history_clean_but_data_checks_pending(client):
    public(client)
    client.auth._session.post.side_effect = [response({'permissions': READ_PERMISSIONS}), response({}),
        response({'open': {}}), response({'closed': {}, 'count': 0}),
        response({'open': {}}), response({'closed': {}, 'count': 0})]
    report = diagnostic.verify(client)
    assert report['checks']['BalanceEx']['status'] == 'ok'
    assert report['checks']['OpenOrders']['detail'] == 'live_parser_empty'
    assert report['checks']['ClosedOrders']['detail'] == 'empty_terminal_page'
    for name in ('QueryOrders', 'QueryTrades', 'pagination_next', 'ClosedOrders_filter_positive'):
        assert report['checks'][name]['status'] == 'skipped'
    assert report['checks']['ClosedOrders_filter_negative']['status'] == 'ok'
    assert not client.armed and report['result'] == 'incomplete'


def test_public_only_no_private_call(client):
    public(client)
    report = diagnostic.verify(client, public_only=True)
    client.auth._session.post.assert_not_called()
    assert report['checks']['private']['status'] == 'skipped'


@pytest.mark.parametrize('value', [{}, {'next': ''}, {'next': 4}, 'token'])
def test_history_bad_cursor_blocks(client, value):
    client.verify_permissions()
    client.auth._session.post.return_value = response({'closed': {}, 'cursor': value})
    with pytest.raises(RuntimeError):
        client.closed_history(5)


def test_history_actual_cursor_and_empty_last_page(client):
    client.verify_permissions()
    client.auth._session.post.side_effect = [response({'closed': {'order1': row('canceled', '0')}, 'cursor': {'next': 'token'}}),
                                           response({'closed': {}, 'count': 0})]
    assert len(client.closed_history(5)) == 1
    args = parse_qs(client.auth._session.post.call_args.kwargs['data'])
    assert args['cursor'] == ['token'] and args['with_cursor'] == ['true']


def test_history_page_limit_is_failure(client):
    client.verify_permissions()
    client.auth._session.post.return_value = response({'closed': {}, 'cursor': {'next': 'token'}})
    with pytest.raises(RuntimeError, match='Plafond'):
        client.closed_history(1)


def test_duplicate_history_is_failure(client):
    client.verify_permissions()
    page = {'closed': {'order1': row('canceled', '0')}, 'cursor': {'next': 'token'}}
    client.auth._session.post.return_value = response(page)
    with pytest.raises(RuntimeError):
        client.closed_history(5)


def test_report_never_contains_exception_response_or_account_data(client):
    public(client)
    client.auth._session.post.side_effect = requests.Timeout('fakeKey c2VjcmV0 secret-signature nonce=12345 order-ABCDEFG')
    report = diagnostic.verify(client)
    rendered = json.dumps(report)
    for secret in ('fakeKey', 'c2VjcmV0', 'secret-signature', '12345', 'order-ABCDEFG'):
        assert secret not in rendered
    assert report['checks']['permissions']['status'] == 'failed'


def test_no_journal_or_user_file_created(client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    public(client)
    diagnostic.verify(client, public_only=True)
    assert list(tmp_path.iterdir()) == []


def test_cli_missing_credentials_sanitized(monkeypatch, capsys):
    def unavailable():
        raise RuntimeError('realKey realSecret')
    monkeypatch.setattr(diagnostic.ReadOnlyValidationClient, 'from_env', unavailable)
    assert diagnostic.main([]) == 1
    report = capsys.readouterr().out
    assert 'realKey' not in report and 'realSecret' not in report
    assert json.loads(report)['calls'] == []


def test_real_data_endpoints_and_report_no_full_ids_or_values(client):
    public(client)
    r = row()
    trade = {'ordertxid': 'real-order-id-example', 'vol': '.0001', 'cost': '5', 'fee': '.02'}
    client.auth._session.post.side_effect = [response({'permissions': READ_PERMISSIONS}), response({}),
        response({'open': {}}), response({'closed': {'real-order-id-example': r}}),
        response({'open': {}}), response({'closed': {}}),  # sonde négative
        response({'real-order-id-example': r}), response({'trade-1': trade}),  # QueryOrders et QueryTrades
        response({'trade-1': trade}),  # parseur _snapshot réutilisé
        response({'open': {}}), response({'closed': {'real-order-id-example': r}}), response({'trade-1': trade})]
    report = diagnostic.verify(client)
    for name in ('QueryOrders', 'QueryTrades', 'order_snapshot', 'ClosedOrders_filter_positive'):
        assert report['checks'][name]['status'] == 'ok'
    assert 'real-order-id-example' not in json.dumps(report)
    assert 'client-1' not in json.dumps(report)
    assert report['checks']['pagination_next']['status'] == 'skipped'
    assert report['result'] == 'incomplete'


def test_factory_uses_readonly_loader_never_live_env(monkeypatch):
    monkeypatch.setenv('KRAKEN_LIVE_API_KEY', 'mustNotUseLiveKey')
    monkeypatch.setenv('KRAKEN_LIVE_API_SECRET', 'not-a-real-secret')
    loader = Mock(return_value=('readOnlyKey', 'c2VjcmV0'))
    monkeypatch.setattr(diagnostic, '_credentials_from_env', loader)
    client = diagnostic.ReadOnlyValidationClient.from_env()
    try:
        assert client.auth._api_key == 'readOnlyKey'
        loader.assert_called_once()
        assert loader.call_args.args[0].name == '.env'
        assert not client.armed
    finally:
        client.auth.close()


def test_successful_report_omits_raw_key_info_and_values(client):
    public(client)
    client.auth._session.post.side_effect = [response({'permissions': READ_PERMISSIONS,
        'api_key': 'fakeKey', 'api_key_name': 'private-label', 'nonce': '987654321', 'iban': 'sensitive-account'}),
        response({'ZEUR': {'balance': '12345.6789', 'hold_trade': '0'}}), response({'open': {}}),
        response({'closed': {}}), response({'open': {}}), response({'closed': {}})]
    report = json.dumps(diagnostic.verify(client))
    for value in ('fakeKey', 'private-label', '987654321', 'sensitive-account', '12345.6789'):
        assert value not in report


def test_public_wire_schema_matches_existing_live_parsers(client):
    asset = {'error': [], 'result': {'XXBTZEUR': {'status': 'online', 'ordermin': '.00006', 'costmin': '.45', 'lot_decimals': 8, 'tick_size': '.1'}}}
    ticker = {'error': [], 'result': {'XXBTZEUR': {'b': ['50000', '1', '1'], 'a': ['50001', '1', '1']}}}
    client.auth._session.get.side_effect = [Mock(raise_for_status=Mock(), json=Mock(return_value=asset)),
                                           Mock(raise_for_status=Mock(), json=Mock(return_value=ticker))]
    report = diagnostic.verify(client, public_only=True)
    assert report['checks']['AssetPairs']['status'] == 'ok' and report['checks']['Ticker']['status'] == 'ok'
    assert [entry['endpoint'] for entry in report['calls']] == ['AssetPairs', 'Ticker']
    for call in client.auth._session.get.call_args_list:
        assert call.kwargs['params'] == {'pair': 'XBTEUR'}
        assert call.kwargs['allow_redirects'] is False
    client.auth._session.post.assert_not_called()


def test_private_snapshot_inconsistency_is_not_success(client):
    public(client)
    r = row()
    trade = {'ordertxid': 'order1', 'vol': '.0001', 'cost': '6', 'fee': '.02'}
    client.auth._session.post.side_effect = [response({'permissions': READ_PERMISSIONS}), response({}),
        response({'open': {}}), response({'closed': {'order1': r}}),
        response({'open': {}}), response({'closed': {}}),
        response({'order1': r}), response({'trade-1': trade}), response({'trade-1': trade}),
        response({'open': {}}), response({'closed': {'order1': r}}), response({'trade-1': trade})]
    report = diagnostic.verify(client)
    assert report['checks']['order_snapshot']['status'] == 'failed'
    assert report['result'] == 'incomplete' and not client.armed
