import json
import math
from unittest.mock import Mock

import numpy as np
import pandas as pd
import pytest

from backtest.experiment import run_experiment
from backtest.report import DEFAULT_CONFIG, run_strategy
from strategies import get_strategies

RULES = {'ordermin': '0', 'costmin': '0', 'lot_decimals': 8}


def data(interval=5, periods=100):
    prices = np.concatenate([np.arange(100, 150), np.arange(150, 100, -1)])[:periods].astype(float)
    return pd.DataFrame({'timestamp': pd.date_range('2026-01-01', periods=len(prices), freq=f'{interval}min', tz='UTC'),
                         'open': prices, 'high': prices + 1, 'low': prices - 1, 'close': prices, 'volume': 1})


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr('requests.sessions.Session.request', Mock(side_effect=AssertionError('No network')))


def run(name='moving_average', **kwargs):
    kwargs.setdefault('data', data())
    kwargs.setdefault('market_rules', RULES)
    return run_experiment(name, **kwargs)


@pytest.mark.parametrize('name', ['moving_average', 'momentum', 'rsi'])
def test_defaults_equal_batch_engine(name):
    frame = data()
    expected = run_strategy(get_strategies()[name], frame, {**DEFAULT_CONFIG, 'market_rules': RULES})
    actual = run(name, data=frame)
    assert actual['status'] == 'completed'
    for key, value in expected['summary'].items():
        assert actual['metrics'][key] == pytest.approx(value) if isinstance(value, (int, float)) else actual['metrics'][key] == value
    assert actual['metrics']['trade_count'] == len(expected['trades'])
    json.dumps(actual, allow_nan=False)


def test_parameters_isolated_experiments_change_signals_not_defaults():
    before = run()
    changed = run(parameters={'short_window': 2, 'long_window': 5})
    after = run()
    assert changed['status'] == 'completed'
    assert changed['details']['trades'] != before['details']['trades']
    assert before == after
    assert changed['strategy']['parameters'] == {'short_window': 2, 'long_window': 5}
    assert changed['dataset']['sha256'] == before['dataset']['sha256']


@pytest.mark.parametrize('name,parameters', [('momentum', {'lookback': 4}), ('rsi', {'period': 4, 'oversold': 20, 'overbought': 80})])
def test_parameterized_existing_strategies(name, parameters):
    result = run(name, parameters=parameters)
    assert result['status'] == 'completed'
    assert all(result['strategy']['parameters'][k] == v for k, v in parameters.items())


@pytest.mark.parametrize('kwargs', [dict(parameters={'unknown': 1}), dict(parameters={'short_window': 50}),
    dict(parameters={'long_window': 3.5}), dict(parameters={'long_window': True}), dict(parameters={'long_window': float('nan')}),
    dict(parameters=[]), dict(config={'capital': float('inf')}), dict(config={'capital': 0}), dict(config={'leverage': 2}),
    dict(interval=2), dict(symbol='../EUR'), dict(start='invalid'), dict(start='2026-02-01', end='2026-01-01')])
def test_invalid_requests_return_structured_error(kwargs):
    result = run(**kwargs)
    assert result['status'] == 'error' and result['metrics'] is None and result['error']
    json.dumps(result, allow_nan=False)


def test_unknown_strategy_structured():
    assert run('no_such_strategy')['status'] == 'error'


def test_timeframe_and_period_are_effective_and_reported():
    frame = data(interval=15)
    result = run(data=frame, interval=15, start='2026-01-01T02:00Z', end='2026-01-02T00:00Z', parameters={'long_window': 5, 'short_window': 2}, symbol='ETHEUR')
    assert result['status'] == 'completed'
    assert result['dataset']['symbol'] == 'ETHEUR'
    assert result['dataset']['effective_start'] == '2026-01-01T02:00:00+00:00'
    assert result['dataset']['effective_end'] == '2026-01-02T00:00:00+00:00'
    assert all('2026-01-01T02:00:00' <= t['timestamp'] < '2026-01-02T00:00:00' for t in result['details']['trades'])


def test_five_minute_strategy_rejects_other_timeframe():
    assert run('trend_breakout', interval=15)['status'] == 'error'


@pytest.mark.parametrize('damage', ['gap', 'duplicate', 'reverse', 'nan', 'infinity', 'negative_volume', 'bad_high'])
def test_invalid_datasets_fail_closed(damage):
    frame = data()
    if damage == 'gap':
        frame = frame.drop(40)
    elif damage == 'duplicate':
        frame.loc[40, 'timestamp'] = frame.loc[39, 'timestamp']
    elif damage == 'reverse':
        frame = frame.iloc[::-1]
    elif damage in ('nan', 'infinity'):
        frame.loc[40, 'close'] = float('nan') if damage == 'nan' else float('inf')
    elif damage == 'negative_volume':
        frame.loc[40, 'volume'] = -1
    else:
        frame.loc[40, 'high'] = 1
    assert run(data=frame)['status'] == 'error'


def test_open_candle_excluded_and_inputs_unchanged():
    frame = data()
    now = pd.Timestamp.now(tz='UTC').floor('5min')
    frame['timestamp'] = pd.date_range(end=now, periods=len(frame), freq='5min')
    original = frame.copy(deep=True)
    result = run(data=frame)
    assert result['status'] == 'completed' and result['dataset']['rows'] == len(frame) - 1
    pd.testing.assert_frame_equal(frame, original)


def test_next_open_execution_and_fees_unchanged():
    result = run(parameters={'short_window': 2, 'long_window': 3}, config={'fee_rate': .01, 'slippage_rate': .02})
    frame = data()
    trade = result['details']['trades'][0]
    # Premier signal après 3 clôtures, exécution sur la quatrième ouverture.
    assert trade['timestamp'] == frame.iloc[3]['timestamp'].isoformat()
    assert trade['price'] == pytest.approx(frame.iloc[3]['open'] * 1.02)
    assert trade['fee'] == pytest.approx(trade['price'] * trade['quantity'] * .01)
    assert trade['quantity'] * trade['price'] + trade['fee'] == pytest.approx(5)


def test_sharpe_formula_and_flat_undefined():
    result = run()
    equity = np.array([p['equity'] for p in result['details']['equity_curve']])
    returns = np.diff(equity) / equity[:-1]
    expected = np.mean(returns) / np.std(returns, ddof=1) * math.sqrt(365 * 24 * 60 / 5)
    assert result['metrics']['sharpe_ratio'] == pytest.approx(expected)
    flat = data()
    for key in ('open', 'high', 'low', 'close'):
        flat[key] = 100
    assert run(data=flat)['metrics']['sharpe_ratio'] is None


def test_local_cache_only_no_rules_network_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr('backtest.experiment.DATA_DIR', tmp_path)
    monkeypatch.setattr('backtest.experiment.load_ohlcv', lambda symbol, interval: data())
    result = run_experiment('moving_average')
    assert result['status'] == 'error' and 'Snapshot' in result['error']['message']
    (tmp_path / 'XBTEUR_rules.json').write_text(json.dumps(RULES))
    assert run_experiment('moving_average')['status'] == 'completed'


def test_short_period_error_and_empty_period():
    assert run(end='2026-01-01T00:10Z')['status'] == 'error'
    assert run(end='2025-01-01')['status'] == 'error'


def test_cli_single_json_and_batch_preserved(monkeypatch, capsys):
    from backtest import __main__ as cli
    monkeypatch.setattr(cli, 'run_experiment', lambda *a, **kw: run(parameters=kw.get('parameters') or a[1]))
    monkeypatch.setattr('sys.argv', ['backtest', '--strategy', 'moving_average', '--params', '{"short_window":2,"long_window":5}', '--json'])
    assert cli.main() == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'completed'
    monkeypatch.setattr(cli, 'get_strategies', lambda: {'moving_average': get_strategies()['moving_average']})
    monkeypatch.setattr(cli, 'load_ohlcv', lambda *a: data())
    monkeypatch.setattr(cli, 'get_pair_rules', lambda *a, **kw: RULES)
    monkeypatch.setattr('sys.argv', ['backtest'])
    cli.main()
    assert 'P&L' in capsys.readouterr().out


def test_no_forced_liquidation_and_open_position_valued():
    frame = data(periods=50)
    result = run('momentum', data=frame)
    assert result['metrics']['open_position']
    assert result['metrics']['closed_trades'] == 0 and result['metrics']['trade_count'] == 1
    assert result['details']['trades'][-1]['side'] == 'BUY'
    assert result['metrics']['pnl'] == pytest.approx(result['metrics']['final_equity'] - 50)


def test_strategy_exception_structured_without_raw_exception(monkeypatch):
    specs = get_strategies()
    def broken(history):
        raise RuntimeError('raw implementation detail')
    specs['broken'] = {**specs['moving_average'], 'function': broken, 'warmup': 1}
    monkeypatch.setattr('backtest.experiment.get_strategies', lambda: specs)
    result = run('broken')
    assert result['status'] == 'error' and result['error']['type'] == 'RuntimeError'
    assert 'raw implementation detail' not in json.dumps(result)


def test_snapshot_pair_mismatch_and_nonfinite_extra_fields():
    assert run(market_rules={**RULES, 'symbol': 'ETHEUR'})['status'] == 'error'
    result = run(market_rules={**RULES, 'extra': float('nan')})
    assert result['status'] == 'error'
    json.dumps(result, allow_nan=False)


def test_cli_unknown_strategy_returns_json_error(monkeypatch, capsys):
    from backtest import __main__ as cli
    monkeypatch.setattr('sys.argv', ['backtest', '--strategy', 'missing', '--json'])
    assert cli.main() == 1
    assert json.loads(capsys.readouterr().out)['status'] == 'error'


def test_cli_rejects_experiment_options_without_strategy(monkeypatch):
    from backtest import __main__ as cli
    monkeypatch.setattr('sys.argv', ['backtest', '--params', '[]'])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2


@pytest.mark.parametrize('name', ['moving_average', 'momentum', 'rsi'])
def test_default_signals_match_previous_formulas(name):
    frame = data()
    strategy = get_strategies()[name]['function']
    for length in range(1, len(frame) + 1):
        history = frame.iloc[:length]
        close = history['close']
        if name == 'moving_average':
            expected = 0 if length < 30 else (1 if close.iloc[-10:].mean() > close.iloc[-30:].mean() else -1)
        elif name == 'momentum':
            expected = 0 if length < 13 else (1 if close.iloc[-1] > close.iloc[-13] else -1)
        elif length < 15:
            expected = 0
        else:
            changes = close.iloc[-15:].diff().dropna()
            gain, loss = changes.clip(lower=0).mean(), -changes.clip(upper=0).mean()
            rsi = (100 if gain > 0 else 50) if loss == 0 else 100 - 100 / (1 + gain / loss)
            expected = 1 if rsi < 30 else (-1 if rsi > 70 else 0)
        assert strategy(history) == expected


@pytest.mark.parametrize('refresh,fail_refresh,expected', [
    (False, False, 'ExecutionError'),
    (True, False, 'ExecutionError'),
    (True, True, 'RefreshError'),
])
def test_cli_error_category_matches_failed_stage(monkeypatch, capsys, refresh, fail_refresh, expected):
    from backtest import __main__ as cli
    refresh_data = Mock(side_effect=RuntimeError('raw private detail') if fail_refresh else None)
    refresh_rules = Mock(return_value=RULES)
    execute = Mock(side_effect=RuntimeError('raw private detail'))
    monkeypatch.setattr(cli, 'get_ohlcv', refresh_data)
    monkeypatch.setattr(cli, 'get_pair_rules', refresh_rules)
    monkeypatch.setattr(cli, 'run_experiment', execute)
    argv = ['backtest', '--strategy', 'moving_average', '--json']
    if refresh:
        argv.append('--refresh')
    monkeypatch.setattr('sys.argv', argv)
    assert cli.main() == 1
    output = capsys.readouterr().out
    result = json.loads(output)
    assert result['status'] == 'error' and result['error']['type'] == expected
    assert result['metrics'] is None and result['details'] is None
    assert 'raw private detail' not in output
    assert refresh_data.call_count == int(refresh)
    assert execute.call_count == int(not fail_refresh)


def test_sharpe_keeps_zero_returns_during_indicator_warmup():
    result = run(parameters={'short_window': 2, 'long_window': 20})
    assert result['status'] == 'completed'
    equity = np.array([p['equity'] for p in result['details']['equity_curve']])
    returns = np.diff(equity) / equity[:-1]
    warmup = result['dataset']['warmup_candles']
    # Pas de position avant le signal de la 20e clôture, exécuté sur l'open suivant.
    assert len(returns) == result['dataset']['rows'] - 1
    assert np.all(returns[:warmup - 1] == 0)
    assert np.any(returns[warmup - 1:] != 0)
    annualization = math.sqrt(365 * 24 * 60 / result['dataset']['interval_minutes'])
    expected_full = returns.mean() / returns.std(ddof=1) * annualization
    trading_only = returns[warmup - 1:]
    expected_without_warmup = trading_only.mean() / trading_only.std(ddof=1) * annualization
    assert result['metrics']['sharpe_ratio'] == pytest.approx(expected_full)
    assert result['metrics']['sharpe_ratio'] != pytest.approx(expected_without_warmup)


@pytest.mark.parametrize('offset', ['1min', '1s', '1ns'])
def test_equally_spaced_but_off_grid_candles_rejected(offset):
    frame = data()
    frame['timestamp'] += pd.Timedelta(offset)
    assert frame['timestamp'].diff().dropna().eq(pd.Timedelta(minutes=5)).all()
    result = run(data=frame)
    assert result['status'] == 'error'
    assert 'alignés' in result['error']['message']


@pytest.mark.parametrize('interval', [1, 5, 15, 30, 60, 240, 1440, 10080, 21600])
def test_aligned_candle_grid_supported_intervals(interval):
    frame = data(interval=interval)
    origin = pd.Timestamp('2020-01-01', tz='UTC').floor(f'{interval}min')
    frame['timestamp'] = pd.date_range(origin, periods=len(frame), freq=f'{interval}min')
    result = run(data=frame, interval=interval)
    assert result['status'] == 'completed'


def test_alignment_uses_utc_not_input_timezone():
    frame = data(interval=60)
    frame['timestamp'] = frame['timestamp'].dt.tz_convert('Asia/Kathmandu')
    assert run(data=frame, interval=60)['status'] == 'completed'
