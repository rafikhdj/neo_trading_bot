from decimal import Decimal as D
from unittest.mock import Mock
import sqlite3

import pandas as pd
import pytest

from live_trading.engine import Engine, Limits
from live_trading.simulated import SimulatedKraken
from live_trading.kraken import KrakenExecutionClient


def buy(data):
    return 1


def sell(data):
    return -1


def candles(end=1800000000):
    frame = pd.DataFrame({'timestamp': pd.date_range(end=pd.Timestamp(end - 300, unit='s', tz='UTC'), periods=30, freq='5min')})
    for col in ('open', 'high', 'low', 'close'):
        frame[col] = 50000
    frame['volume'] = 1
    return frame


@pytest.fixture
def setup(tmp_path):
    broker = SimulatedKraken(tmp_path / 'exchange.json')
    engine = Engine(tmp_path / 'live.sqlite', broker, buy, 'test')
    yield engine, broker
    engine.close()


def start(engine):
    assert engine.activate(now=1799999999)


def test_default_disabled(setup):
    e, b = setup
    assert e.cycle(candles(), now=1800000000) is None
    assert not b.armed and not e.journal.orders()


def test_buy_sell_equity_fees(setup):
    e, b = setup
    start(e)
    e.cycle(candles(), now=1800000000)
    p = e.portfolio()
    assert p['btc'] > 0 and p['eur'] < 1000 and p['fees'] > 0
    assert p['eur'] + p['btc'] * b.price == D(1000) - p['fees']
    # Identical strategy identity; the callback models a later exit signal.
    e.strategy = sell
    e.cycle(candles(1800000300), now=1800000300)
    after = e.portfolio()
    assert after['btc'] < p['btc'] and after['eur'] > p['eur']
    assert after['fees'] > p['fees']
    assert after['eur'] + after['btc'] * b.price == D(1000) - after['fees']
    assert e.reconcile()


@pytest.mark.parametrize('behavior,status', [('open','open'), ('transient_partial','partially_filled'), ('rejected','rejected'), ('canceled','canceled')])
def test_statuses(setup, behavior, status):
    e, b = setup
    b.behavior = behavior
    start(e)
    e.cycle(candles(), now=1800000000)
    assert e.journal.orders()[0]['status'] == status
    assert bool(e.journal.fills()) == (behavior == 'transient_partial')
    if behavior == 'transient_partial':
        p = e.portfolio()
        assert e.cancel(e.journal.orders()[0]['id'])
        assert e.portfolio() == p
        assert e.journal.orders()[0]['status'] == 'canceled'


def test_timeout_restart_and_duplicates(setup):
    e, b = setup
    b.behavior = 'timeout'
    start(e)
    e.cycle(candles(), now=1800000000)
    assert not e.enabled and e.journal.orders()[0]['status'] == 'unknown'
    path = e.journal.path
    e.close()
    reopened = Engine(path, SimulatedKraken(b.path), buy, 'test')
    try:
        assert not reopened.enabled
        assert reopened.reconcile()
        assert reopened.journal.orders()[0]['status'] == 'filled'
        assert len(reopened.journal.fills()) == 1
        assert reopened.activate(now=1800000001)
        reopened.cycle(candles(), now=1800000002)
        assert len(reopened.journal.orders()) == 1
    finally:
        reopened.close()
    # Fixture finalizer may close an already closed connection (sqlite permits it).


def test_unknown_missing_never_retried(setup):
    e, b = setup
    start(e)
    b.submit = Mock(side_effect=TimeoutError())
    e.cycle(candles(), now=1800000000)
    assert not e.reconcile() and not e.activate(now=1800000001)
    assert b.submit.call_count == 1
    assert not e.journal.fills()


def test_crash_after_sending(setup):
    e, b = setup
    start(e)
    order = dict(id='crash', candle='1', strategy='test', side='buy', quantity='.0001', price='50100')
    e.journal.prepare(order)
    e.journal.update('crash', 'sending')
    b.submit(order)
    path = e.journal.path
    e.close()
    new = Engine(path, SimulatedKraken(b.path), buy, 'test')
    try:
        assert new.journal.orders()[0]['status'] == 'unknown'
        assert new.reconcile()
        assert new.portfolio()['btc'] == D('.0001')
        assert not new.enabled
    finally:
        new.close()


def test_network_failure_no_fiction(setup):
    e, b = setup
    start(e)
    b.balances = Mock(side_effect=ConnectionError('secret should never appear'))
    e.cycle(candles(), now=1800000000)
    assert not e.enabled and not e.journal.orders() and not e.journal.fills()
    assert 'secret' not in e.reason


def test_one_closed_candle_once(setup):
    e, b = setup
    start(e)
    b.behavior = 'rejected'
    e.cycle(candles(), now=1800000000)
    assert e.activate(now=1800000001)
    e.cycle(candles(), now=1800000002)
    assert len(e.journal.orders()) == 1


@pytest.mark.parametrize('offset', [-300, 300])
def test_stale_or_open_candle(setup, offset):
    e, b = setup
    start(e)
    e.cycle(candles(1800000000 + offset), now=1800000000)
    assert not e.enabled and not e.journal.orders()


def test_no_replay_after_activation(setup):
    e, b = setup
    assert e.activate(now=1800000001)
    e.cycle(candles(), now=1800000002)
    assert not e.journal.orders()


def test_kill_does_not_liquidate(setup):
    e, b = setup
    start(e)
    e.cycle(candles(), now=1800000000)
    portfolio = e.portfolio()
    e.kill_path.touch()
    e.cycle(candles(1800000300), now=1800000300)
    assert not e.enabled and e.portfolio() == portfolio
    assert not e.activate(now=1800000301)


def test_insufficient_balance(setup):
    e, b = setup
    b.state['eur'] = '.1'
    start(e)
    e.cycle(candles(), now=1800000000)
    assert not e.enabled and not e.journal.orders()


def test_divergence_blocks(setup):
    e, b = setup
    start(e)
    b.state['eur'] = '999'
    e.cycle(candles(), now=1800000000)
    assert not e.enabled and not e.journal.orders()


def test_position_and_order_caps(setup):
    e, b = setup
    start(e)
    e.cycle(candles(), now=1800000000)
    o = e.journal.orders()[0]
    assert D(o['quantity']) * D(o['price']) * D('1.01') <= e.limits.per_order
    e.cycle(candles(1800000300), now=1800000300)
    assert len(e.journal.orders()) == 1


def test_loss_drawdown_blocks(setup):
    e, b = setup
    start(e)
    e.cycle(candles(), now=1800000000)
    e.limits = Limits(daily_loss=D('.01'))
    assert not e.activate(now=1800000001)


def test_pending_prevents_second_order(setup):
    e, b = setup
    b.behavior = 'open'
    start(e)
    e.cycle(candles(), now=1800000000)
    e.cycle(candles(1800000300), now=1800000300)
    assert len(e.journal.orders()) == 1 and not e.journal.fills()


def test_lock_and_independence(setup, tmp_path):
    e, b = setup
    with pytest.raises(RuntimeError):
        Engine(e.journal.path, b, buy, 'other')
    assert not (tmp_path / 'paper_trading').exists()
    assert 'paper_trading' not in '\n'.join(__import__('live_trading.engine', fromlist=['']).__dict__)


def test_strategy_change_blocks(setup):
    e, b = setup
    path = e.journal.path
    e.close()
    new = Engine(path, b, sell, 'test')
    try:
        assert not new.activate(now=1800000000)
    finally:
        new.close()


def test_real_client_disabled_and_separate_env(monkeypatch):
    monkeypatch.setenv('KRAKEN_API_KEY', 'readOnly')
    monkeypatch.setenv('KRAKEN_API_SECRET', 'c2VjcmV0')
    monkeypatch.delenv('KRAKEN_LIVE_API_KEY', raising=False)
    monkeypatch.delenv('KRAKEN_LIVE_API_SECRET', raising=False)
    with pytest.raises(Exception):
        KrakenExecutionClient.from_env()
    client = KrakenExecutionClient('fakeKey', 'c2VjcmV0')
    client.auth._session.post = Mock(side_effect=AssertionError('No network'))
    with pytest.raises(RuntimeError):
        client.submit(dict(side='buy',quantity='.001',price='1',id='x'))
    with pytest.raises(RuntimeError):
        client.cancel('x')
    client.auth._session.post.assert_not_called()


def test_write_failure_before_send_blocks(setup, monkeypatch):
    e, b = setup
    start(e)
    submit = Mock(wraps=b.submit)
    b.submit = submit
    monkeypatch.setattr(e.journal, 'prepare', Mock(side_effect=sqlite3.OperationalError('disk full')))
    e.cycle(candles(), now=1800000000)
    assert not e.enabled and not e.journal.orders()
    submit.assert_not_called()


def test_write_failure_after_accept_recovers(setup, monkeypatch):
    e, b = setup
    start(e)
    original = e.journal.update
    def fail(oid, status, **kw):
        if status == 'open':
            raise sqlite3.OperationalError('disk full')
        return original(oid, status, **kw)
    monkeypatch.setattr(e.journal, 'update', fail)
    e.cycle(candles(), now=1800000000)
    assert not e.enabled and e.journal.orders()[0]['status'] == 'sending'
    monkeypatch.setattr(e.journal, 'update', original)
    assert e.reconcile()
    assert len(e.journal.fills()) == 1


def test_unfilled_order_later_fills_once(setup):
    e, b = setup
    start(e)
    b.behavior = 'transient_partial'
    oid = e.cycle(candles(), now=1800000000)
    partial = e.portfolio()['btc']
    b.fill(oid)
    assert e.reconcile() and e.reconcile()
    assert e.portfolio()['btc'] == partial * 2
    assert len(e.journal.fills()) == 2


def test_runtime_file_change_pauses(setup, tmp_path):
    e, b = setup
    start(e)
    source = tmp_path / 'strategy.py'
    source.write_text('modified')
    e.source_path = str(source)
    e.cycle(candles(), now=1800000000)
    assert not e.enabled and not e.journal.orders()


def test_paper_state_untouched(setup, tmp_path):
    e, b = setup
    paper = tmp_path / 'paper_trading' / 'states' / 'test.json'
    paper.parent.mkdir(parents=True)
    paper.write_text('{"cash":50,"btc":0,"status":"running"}')
    before = paper.read_bytes()
    start(e)
    e.cycle(candles(), now=1800000000)
    assert paper.read_bytes() == before
    value = e.valuation(b.price)
    assert value['pnl'] == -value['fees']


def test_daily_and_drawdown_independently(setup):
    e, b = setup
    start(e)
    e.cycle(candles(), now=1800000000)
    b.price = D('1')
    e.limits = Limits(daily_loss=D('1000'), drawdown=D('.01'))
    assert not e.activate(now=1800000001)


def test_fresh_quote_required(setup):
    e, b = setup
    start(e)
    b.quote = lambda now: {'bid': D(50000), 'ask': D(50000), 'time': now - 31}
    e.cycle(candles(), now=1800000000)
    assert not e.enabled and not e.journal.orders()


def test_kill_between_prepare_and_send(setup, monkeypatch):
    e, b = setup
    start(e)
    original = e.journal.update
    def update(oid, status, **kw):
        original(oid, status, **kw)
        if status == 'sending':
            e.kill_path.touch()
    monkeypatch.setattr(e.journal, 'update', update)
    b.submit = Mock(wraps=b.submit)
    e.cycle(candles(), now=1800000000)
    b.submit.assert_not_called()
    assert not e.enabled and e.journal.orders()[0]['status'] == 'rejected'


def test_prepared_is_abandoned_on_restart(setup):
    e, b = setup
    start(e)
    e.journal.prepare(dict(id='prepared', candle='1', strategy='test', side='buy', quantity='.0001', price='50100'))
    path = e.journal.path
    e.close()
    new = Engine(path, b, buy, 'test')
    try:
        assert new.journal.orders()[0]['status'] == 'rejected'
        assert new.reconcile() and not new.enabled
        assert not new.journal.fills()
    finally:
        new.close()


@pytest.mark.parametrize('field', ['minimum', 'costmin', 'step', 'tick'])
@pytest.mark.parametrize('value', ['NaN', 'Infinity', '-1', '0', None, 'invalid'])
def test_invalid_rules_checked_before_position_or_hold(setup, field, value):
    e, b = setup
    start(e)
    rules = b.rules()
    rules[field] = value
    b.rules = lambda: rules
    e.strategy = lambda data: 0
    e.cycle(candles(), now=1800000000)
    assert not e.enabled and not e.journal.orders()


def test_allocated_equity_and_historical_drawdown_across_utc_midnight(setup):
    e, b = setup
    e.limits = Limits(daily_loss=D('1000'), drawdown=D('.1'))
    before = pd.Timestamp('2026-11-03T23:59:59Z').timestamp()
    assert e.activate(now=before)
    assert D(e.journal.get('risk')['start']) == 100  # Solde 1000, capital alloué 100.
    e.portfolio = lambda: {'eur': D(1020), 'btc': D(0), 'fees': D(0)}
    e.risk(before)
    e.portfolio = lambda: {'eur': D(1010), 'btc': D(0), 'fees': D(0)}
    e.risk(before + 1)
    stats = e.journal.get('risk')
    assert stats['day'] == '2026-11-04'
    assert D(stats['start']) == 120 and D(stats['peak']) == 120 and D(stats['last']) == 110
    e.portfolio = lambda: {'eur': D(1007), 'btc': D(0), 'fees': D(0)}
    with pytest.raises(RuntimeError, match='drawdown'):
        e.risk(before + 2)


def test_daily_loss_carries_gap_from_last_observation_at_midnight(setup):
    e, b = setup
    before = pd.Timestamp('2026-11-03T23:59:59Z').timestamp()
    assert e.activate(now=before)
    e.portfolio = lambda: {'eur': D(994), 'btc': D(0), 'fees': D(0)}
    with pytest.raises(RuntimeError, match='perte'):
        e.risk(before + 1)
    assert D(e.journal.get('risk')['start']) == 100


@pytest.mark.parametrize('side,price', [('buy','50200'), ('sell','49800')])
def test_simulated_ioc_unmarketable_limit_cancels(side, price):
    b = SimulatedKraken()
    b.arm()
    b.price = D(price)
    before = b.balances()
    oid = 'ioc-limit'
    b.submit(dict(id=oid, side=side, quantity='.0001', price='50000'))
    assert b.find(oid)['status'] == 'canceled' and not b.find(oid)['fills']
    assert b.balances() == before


def test_simulated_ioc_partial_cancels_remainder(setup):
    e, b = setup
    start(e)
    b.behavior = 'partial'
    oid = e.cycle(candles(), now=1800000000)
    assert e.journal.orders()[0]['status'] == 'canceled'
    assert e.portfolio()['btc'] == D(e.journal.orders()[0]['quantity']) / 2
    assert not b.open_orders() and e.reconcile()
    with pytest.raises(RuntimeError, match='terminal'):
        b.fill(oid)


def test_wal_reader_during_uncommitted_writer(setup):
    e, b = setup
    assert e.journal.db.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
    reader = sqlite3.connect(e.journal.path.resolve().as_uri() + '?mode=ro', uri=True, timeout=.1)
    try:
        e.journal.db.execute('BEGIN EXCLUSIVE')
        e.journal.db.execute("INSERT INTO meta VALUES ('uncommitted','true')")
        assert reader.execute("SELECT value FROM meta WHERE key='uncommitted'").fetchone() is None
        assert reader.execute('SELECT count(*) FROM orders').fetchone()[0] == 0
        e.journal.db.commit()
        assert reader.execute("SELECT value FROM meta WHERE key='uncommitted'").fetchone()[0] == 'true'
    finally:
        e.journal.db.rollback()
        reader.close()


@pytest.mark.parametrize('field,value', [('price','99999'), ('quantity','.1'), ('side','sell')])
def test_conflicting_order_identity_cannot_confirm_fills(setup, field, value):
    e, b = setup
    start(e)
    b.behavior = 'open'
    oid = e.cycle(candles(), now=1800000000)
    b.state['orders'][oid][field] = value
    assert not e.reconcile() and not e.enabled
    assert not e.journal.fills()
