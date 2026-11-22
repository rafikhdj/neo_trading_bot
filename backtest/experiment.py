"""Un backtest indépendant, hors ligne par défaut, résultat JSON natif."""
from functools import partial
import hashlib
import inspect
import json
import math
import re

import numpy as np
import pandas as pd

from backtest.report import DEFAULT_CONFIG, run_strategy
from data.kraken import DATA_DIR, load_ohlcv
from strategies import get_strategies


class ExperimentError(ValueError):
    pass


def _timestamp(value):
    stamp = pd.Timestamp(value)
    if pd.isna(stamp):
        raise ExperimentError('Date invalide.')
    return stamp.tz_localize('UTC') if stamp.tzinfo is None else stamp.tz_convert('UTC')


def _configure(name, spec, parameters):
    defaults = {key: p.default for key, p in list(inspect.signature(spec['function']).parameters.items())[1:]
                if p.default is not inspect.Parameter.empty}
    if not isinstance(parameters, dict) or set(parameters) - defaults.keys():
        raise ExperimentError('Paramètres inconnus ou format invalide.')
    effective = {**defaults, **parameters}
    for key, value in effective.items():
        default = defaults[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ExperimentError('Paramètres numériques finis attendus.')
        if isinstance(default, int) and not isinstance(value, int):
            raise ExperimentError('Un paramètre de fenêtre doit être entier.')
    function = partial(spec['function'], **effective)
    # Valide les paramètres exposés avant la simulation, sans toucher aux globals.
    if name in {'moving_average', 'momentum', 'rsi'}:
        try:
            function(pd.DataFrame())
        except ValueError as error:
            raise ExperimentError(str(error)) from None
    warmup = {'moving_average': effective.get('long_window'),
              'momentum': effective.get('lookback', 0) + 1,
              'rsi': effective.get('period', 0) + 1}.get(name, spec['warmup'])
    return {**spec, 'function': function, 'warmup': warmup}, effective


def run_experiment(strategy, parameters=None, *, symbol='XBTEUR', interval=5,
                   start=None, end=None, config=None, data=None, market_rules=None):
    """Dates UTC, bornes [start,end), uniquement bougies entièrement clôturées.

    data et market_rules permettent d'injecter des fixtures sans réseau.
    Sinon : Parquet et snapshot de règles locaux obligatoires. Aucun refresh ici.
    """
    result = {'schema_version': 1, 'status': 'error', 'error': None,
              'strategy': {'name': strategy, 'parameters': {}, 'source_hash': None},
              'dataset': None, 'config': None, 'metrics': None, 'details': None}
    try:
        specs = get_strategies()
        if not isinstance(strategy, str) or strategy not in specs or specs[strategy].get('error'):
            raise ExperimentError('Stratégie inconnue ou impossible à charger.')
        if not isinstance(symbol, str) or not re.fullmatch(r'[A-Za-z0-9]+', symbol):
            raise ExperimentError('Paire Kraken invalide.')
        if type(interval) is not int or interval not in {1, 5, 15, 30, 60, 240, 1440, 10080, 21600}:
            raise ExperimentError('Intervalle Kraken non supporté (minutes).')
        if strategy not in {'moving_average', 'momentum', 'rsi'} and interval != 5:
            raise ExperimentError('Cette stratégie suppose des bougies de 5 minutes.')
        result['strategy']['source_hash'] = specs[strategy]['source_hash']
        spec, effective = _configure(strategy, specs[strategy], {} if parameters is None else parameters)
        result['strategy']['parameters'] = effective
        settings = DEFAULT_CONFIG.copy()
        if config is not None:
            if not isinstance(config, dict) or set(config) - {'capital', 'position_size', 'fee_rate', 'slippage_rate'}:
                raise ExperimentError('Configuration inconnue ou invalide.')
            settings.update(config)
        for key in ('capital', 'position_size', 'fee_rate', 'slippage_rate'):
            value = settings[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ExperimentError('Configuration numérique finie attendue.')
        if settings['capital'] <= 0 or not 0 < settings['position_size'] <= 1 or not all(0 <= settings[k] < 1 for k in ('fee_rate', 'slippage_rate')):
            raise ExperimentError('Capital, taille de position ou taux invalides.')
        settings.update(symbol=symbol, interval=interval)
        result['config'] = settings.copy()
        frame = load_ohlcv(symbol, interval) if data is None else data.copy(deep=True)
        columns = ['open', 'high', 'low', 'close', 'volume']
        if not {'timestamp', *columns}.issubset(frame.columns):
            raise ExperimentError('Colonnes OHLCV manquantes.')
        frame['timestamp'] = pd.to_datetime(frame['timestamp'], utc=True)
        if frame['timestamp'].isna().any() or frame['timestamp'].duplicated().any() or not frame['timestamp'].is_monotonic_increasing:
            raise ExperimentError('Timestamps invalides, dupliqués ou désordonnés.')
        delta = pd.Timedelta(minutes=interval)
        lower = _timestamp(start) if start is not None else None
        upper = _timestamp(end) if end is not None else None
        if lower is not None and upper is not None and lower >= upper:
            raise ExperimentError('start doit précéder end.')
        now = pd.Timestamp.now(tz='UTC')
        mask = frame['timestamp'] + delta <= now
        if lower is not None:
            mask &= frame['timestamp'] >= lower
        if upper is not None:
            mask &= frame['timestamp'] + delta <= upper
        frame = frame.loc[mask, ['timestamp', *columns]].reset_index(drop=True)
        if frame.empty:
            raise ExperimentError('Aucune bougie clôturée dans la période.')
        frame[columns] = frame[columns].apply(pd.to_numeric, errors='raise')
        values = frame[columns].to_numpy(dtype=float)
        if not np.isfinite(values).all() or (values[:, :4] <= 0).any() or (values[:, 4] < 0).any():
            raise ExperimentError('Prix/volumes invalides.')
        epoch = pd.Timestamp('1970-01-01', tz='UTC')
        if not frame['timestamp'].sub(epoch).mod(delta).eq(pd.Timedelta(0)).all():
            raise ExperimentError('Timestamps non alignés sur la grille UTC de l’intervalle demandé.')
        if not frame['timestamp'].diff().dropna().eq(delta).all():
            raise ExperimentError('Trous ou timeframe incohérent dans la période.')
        if ((frame['high'] < frame[['open', 'close', 'low']].max(axis=1)) |
                (frame['low'] > frame[['open', 'close', 'high']].min(axis=1))).any():
            raise ExperimentError('Bornes OHLC incohérentes.')
        payload = json.dumps([[row[0].isoformat(), *map(float, row[1:])]
                              for row in frame.itertuples(index=False, name=None)],
                             separators=(',', ':'), allow_nan=False)
        result['dataset'] = {'symbol': symbol, 'interval_minutes': interval, 'rows': len(frame),
            'source': 'injected' if data is not None else 'local_parquet',
            'sha256': hashlib.sha256(payload.encode()).hexdigest(),
            'requested_start': None if lower is None else lower.isoformat(),
            'requested_end': None if upper is None else upper.isoformat(),
            'effective_start': frame.iloc[0]['timestamp'].isoformat(),
            'effective_end': (frame.iloc[-1]['timestamp'] + delta).isoformat(),
            'warmup_candles': spec['warmup']}
        if len(frame) < spec['warmup'] + 1:
            raise ExperimentError(f"Au moins {spec['warmup'] + 1} bougies nécessaires dans la période.")
        if market_rules is None:
            path = DATA_DIR / f'{symbol}_rules.json'
            if not path.is_file():
                raise ExperimentError('Snapshot de règles local manquant ; actualiser explicitement les données.')
            market_rules = json.loads(path.read_text())
        if market_rules.get('symbol', symbol) != symbol:
            raise ExperimentError('Snapshot de règles associé à une autre paire.')
        for key in ('ordermin', 'costmin'):
            if not math.isfinite(float(market_rules[key])) or float(market_rules[key]) < 0:
                raise ExperimentError('Règles de marché invalides.')
        if type(market_rules['lot_decimals']) is not int or not 0 <= market_rules['lot_decimals'] <= 18:
            raise ExperimentError('Précision de volume invalide.')
        try:
            json.dumps(market_rules, allow_nan=False)
        except (TypeError, ValueError):
            raise ExperimentError('Snapshot de règles non sérialisable ou non fini.') from None
        settings['market_rules'] = dict(market_rules)
        result['config']['market_rules'] = dict(market_rules)
        report = run_strategy(spec, frame, settings)
        equity = np.asarray([p['equity'] for p in report['equity_curve']], dtype=float)
        if not np.isfinite(equity).all() or (equity <= 0).any():
            raise ExperimentError('Equity invalide pour les métriques de risque.')
        returns = np.diff(equity) / equity[:-1]
        std = float(np.std(returns, ddof=1)) if len(returns) >= 2 else 0
        sharpe = float(np.mean(returns) / std * math.sqrt(365 * 24 * 60 / interval)) if std > 0 else None
        result['metrics'] = {**report['summary'], 'sharpe_ratio': sharpe,
                             'trade_count': len(report['trades'])}
        result['details'] = {k: report[k] for k in ('trades', 'equity_curve', 'rejected_orders')}
        result['status'] = 'completed'
        # JSON strict : ni Timestamp numpy, ni NaN/Infinity.
        return json.loads(json.dumps(result, default=lambda v: v.isoformat() if hasattr(v, 'isoformat') else v.item(), allow_nan=False))
    except Exception as error:
        result.update(status='error', metrics=None, details=None,
                      error={'type': type(error).__name__, 'message': str(error) if isinstance(error, ExperimentError) else 'Échec des paramètres, données ou de la stratégie ; vérifier les entrées.'})
        return result
