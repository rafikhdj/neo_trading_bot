"""Backtests de toutes les stratégies sur l'historique local."""
import argparse
import json
import sys

from backtest.experiment import run_experiment

from backtest.report import DEFAULT_CONFIG, run_strategy
from data.kraken import get_ohlcv, load_ohlcv, get_pair_rules
from strategies import get_strategies


def main():
    strategies = get_strategies()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy", help="Une seule stratégie ; toutes par défaut")
    parser.add_argument("--refresh", action="store_true", help="Actualiser l'historique depuis Kraken")
    parser.add_argument("--params", type=json.loads, default={}, help='Objet JSON de paramètres de stratégie')
    parser.add_argument("--symbol", default=DEFAULT_CONFIG["symbol"])
    parser.add_argument("--interval", type=int, default=DEFAULT_CONFIG["interval"], help="Minutes")
    parser.add_argument("--start", help="Début UTC inclus")
    parser.add_argument("--end", help="Fin UTC exclue")
    parser.add_argument("--json", action="store_true", help="Résultat structuré du backtest individuel")
    for key in ("capital", "position_size", "fee_rate", "slippage_rate"):
        parser.add_argument('--' + key.replace('_', '-'), type=float, default=DEFAULT_CONFIG[key])
    args = parser.parse_args()
    if not args.strategy and (args.json or args.params != {} or args.start or args.end
            or args.symbol != DEFAULT_CONFIG['symbol'] or args.interval != DEFAULT_CONFIG['interval']
            or any(getattr(args, k) != DEFAULT_CONFIG[k] for k in ('capital', 'position_size', 'fee_rate', 'slippage_rate'))):
        parser.error('Les options d’expérience exigent --strategy ; le batch historique reste inchangé.')
    if args.strategy:
        stage = 'refresh'
        try:
            if args.refresh:
                get_ohlcv(args.symbol, args.interval)
                get_pair_rules(args.symbol, refresh=True)
            stage = 'execution'
            result = run_experiment(args.strategy, args.params, symbol=args.symbol, interval=args.interval,
                start=args.start, end=args.end, config={k: getattr(args, k) for k in ('capital', 'position_size', 'fee_rate', 'slippage_rate')})
        except Exception:
            result = {'schema_version': 1, 'status': 'error',
                      'strategy': {'name': args.strategy, 'parameters': {}, 'source_hash': None},
                      'dataset': None, 'config': None, 'details': None, 'error': {
                          'type': 'RefreshError' if stage == 'refresh' else 'ExecutionError',
                          'message': 'Actualisation des données impossible.' if stage == 'refresh'
                          else 'Échec inattendu pendant le backtest individuel.'}, 'metrics': None}
        if args.json:
            print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        elif result['status'] == 'completed':
            summary = result['metrics']
            print(f"{args.strategy} | P&L {summary['pnl']:.2f} | rendement {summary['return']:.2%} | "
                  f"drawdown {summary['max_drawdown']:.2%} | trades clôturés {summary['closed_trades']}")
        else:
            print(result['error']['message'], file=sys.stderr)
        return 0 if result['status'] == 'completed' else 1
    config = DEFAULT_CONFIG.copy()
    data = (get_ohlcv if args.refresh else load_ohlcv)(config["symbol"], config["interval"])
    config["market_rules"] = get_pair_rules(config["symbol"], refresh=args.refresh)
    for key, spec in strategies.items():
        if args.strategy and key != args.strategy:
            continue
        result = run_strategy(spec, data, config)["summary"]
        print(f"{spec['name']} | portefeuille {result['final_equity']:.2f} EUR | "
              f"P&L {result['pnl']:.2f} EUR | rendement {result['return']:.2%} | "
              f"drawdown {result['max_drawdown']:.2%} | trades clôturés {result['closed_trades']} | "
              f"tentatives refusées {result['rejected_orders']}")


if __name__ == "__main__":
    sys.exit(main())
