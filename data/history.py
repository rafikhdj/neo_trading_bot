"""Répare les trous internes avec les transactions historiques Kraken."""
import argparse
import logging
import time

import pandas as pd
import requests

from data.kraken import get_ohlcv, load_ohlcv, store_ohlcv

LOG = logging.getLogger(__name__)


def candles_from_trades(trades, start, end, interval):
    """Bougies réelles uniquement : aucune interpolation des périodes sans échange."""
    frame = pd.DataFrame(trades, columns=["price", "volume", "time", "side", "type", "misc", "id"])
    frame = frame.drop_duplicates("id").sort_values(["time", "id"])
    frame["timestamp"] = pd.to_datetime(frame["time"], unit="s", utc=True)
    frame = frame[(frame.timestamp >= start) & (frame.timestamp < end)].copy()
    if frame.empty:
        return pd.DataFrame()
    frame["price"] = pd.to_numeric(frame["price"])
    frame["volume"] = pd.to_numeric(frame["volume"])
    frame["value"] = frame["price"] * frame["volume"]
    grouped = frame.set_index("timestamp").resample(f"{interval}min", origin="epoch")
    candles = grouped["price"].ohlc()
    candles["volume"] = grouped["volume"].sum()
    candles["count"] = grouped["price"].count()
    candles["vwap"] = grouped["value"].sum() / candles["volume"]
    return candles[candles["count"] > 0].reset_index()


def fetch_trade_candles(symbol, interval, start, end, pause=1.0):
    cursor = str(start.value)  # Kraken attend des nanosecondes ou le curseur retourné.
    trades = []
    pages = 0
    with requests.Session() as session:
        while int(cursor) < end.value:
            for attempt in range(4):
                try:
                    response = session.get("https://api.kraken.com/0/public/Trades",
                                           params={"pair": symbol, "since": cursor, "count": 1000}, timeout=30)
                    response.raise_for_status()
                    payload = response.json()
                    if payload["error"]:
                        raise RuntimeError(str(payload["error"]))
                    break
                except (requests.RequestException, RuntimeError):
                    if attempt == 3:
                        raise
                    time.sleep(2 ** (attempt + 1))
            result = payload["result"]
            batch = next(value for key, value in result.items() if key != "last")
            next_cursor = str(result["last"])
            if not batch or int(next_cursor) <= int(cursor):
                raise RuntimeError("Historique Kraken interrompu avant la fin du trou ; fichier conservé.")
            trades.extend(batch)
            cursor = next_cursor
            pages += 1
            if pages % 10 == 0:
                LOG.info("%s : %s transactions, jusqu'à %s", symbol, len(trades),
                         pd.to_datetime(int(cursor), unit="ns", utc=True))
            time.sleep(pause)
    return candles_from_trades(trades, start, end, interval)


def repair_history(symbol="XBTEUR", interval=5, refresh=False):
    data = get_ohlcv(symbol, interval) if refresh else load_ohlcv(symbol, interval)
    step = pd.Timedelta(minutes=interval)
    gaps = data.index[data.timestamp.diff() > step]
    ranges = [(data.iloc[i - 1].timestamp + step, data.iloc[i].timestamp) for i in gaps]
    for start, end in ranges:
        LOG.info("Réparation %s → %s", start, end)
        candles = fetch_trade_candles(symbol, interval, start, end)
        if candles.empty:
            LOG.warning("Aucune transaction trouvée : trou conservé.")
            continue
        data = store_ohlcv(candles, symbol, interval)
        LOG.info("%s bougies importées", len(candles))
    remaining = int((data.timestamp.diff() > step).sum())
    LOG.info("Terminé : %s bougies, %s trou(s) restant(s)", len(data), remaining)
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="Actualiser les dernières bougies avant réparation")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    repair_history(refresh=args.refresh)


if __name__ == "__main__":
    main()
