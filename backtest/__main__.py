"""Backtests de toutes les stratégies sur l'historique local."""
import argparse

from backtest.report import DEFAULT_CONFIG, run_strategy
from data.kraken import get_ohlcv, load_ohlcv
from strategies import get_strategies


def main():
    strategies = get_strategies()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy", choices=list(strategies), help="Une seule stratégie ; toutes par défaut")
    parser.add_argument("--refresh", action="store_true", help="Actualiser l'historique depuis Kraken")
    args = parser.parse_args()
    config = DEFAULT_CONFIG.copy()
    data = (get_ohlcv if args.refresh else load_ohlcv)(config["symbol"], config["interval"])
    for key, spec in strategies.items():
        if args.strategy and key != args.strategy:
            continue
        result = run_strategy(spec, data, config)["summary"]
        print(f"{spec['name']} | portefeuille {result['final_equity']:.2f} EUR | "
              f"P&L {result['pnl']:.2f} EUR | rendement {result['return']:.2%} | "
              f"drawdown {result['max_drawdown']:.2%} | trades clôturés {result['closed_trades']}")


if __name__ == "__main__":
    main()
