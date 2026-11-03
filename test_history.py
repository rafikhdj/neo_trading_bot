import pandas as pd
import pytest

from data import history


START = pd.Timestamp("2026-10-22 14:30", tz="UTC")
END = START + pd.Timedelta(minutes=15)


def trade(minutes, price, volume, ident):
    return [str(price), str(volume), (START + pd.Timedelta(minutes=minutes)).timestamp(), "b", "m", "", ident]


def test_real_trades_aggregate_without_interpolation():
    trades = [trade(0, 100, 1, 1), trade(1, 110, 2, 2), trade(2, 90, 1, 3),
              trade(2, 90, 1, 3), trade(10, 120, 1, 4), trade(15, 999, 1, 5)]
    result = history.candles_from_trades(trades, START, END, 5)
    assert len(result) == 2  # Pas de bougie inventée pour la période sans échange.
    first = result.iloc[0]
    assert (first.open, first.high, first.low, first.close) == (100, 110, 90, 90)
    assert first.volume == 4
    assert first["count"] == 3
    assert first.vwap == pytest.approx(102.5)
    assert result.iloc[-1].timestamp == START + pd.Timedelta(minutes=10)


def test_trade_pagination_uses_server_cursor(monkeypatch):
    requested = []
    cursor = str((START + pd.Timedelta(minutes=5)).value)
    pages = iter([
        {"error": [], "result": {"PAIR": [trade(1, 100, 1, 1)], "last": cursor}},
        {"error": [], "result": {"PAIR": [trade(11, 110, 2, 2), trade(15, 200, 1, 3)], "last": str(END.value)}},
    ])
    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return next(pages)
    class Session:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def get(self, url, params, timeout):
            requested.append(params["since"])
            return Response()
    monkeypatch.setattr(history.requests, "Session", Session)
    result = history.fetch_trade_candles("XBTEUR", 5, START, END, pause=0)
    assert requested == [str(START.value), cursor]
    assert list(result.close) == [100, 110]


def test_repair_fills_gap_and_preserves_existing_data(tmp_path, monkeypatch):
    from data.kraken import prepare_ohlcv
    candles = history.candles_from_trades([trade(0, 100, 1, 1), trade(10, 120, 1, 2)], START, END, 5)
    candles["vwap"] = candles["vwap"].astype(str)  # Ancien format Kraken.
    missing = history.candles_from_trades([trade(5, 110, 1, 3)], START, END, 5)
    monkeypatch.setattr(history, "DATA_DIR", tmp_path)
    monkeypatch.setattr(history, "load_ohlcv", lambda *a: prepare_ohlcv(candles))
    calls = []
    def fetch(symbol, interval, start, end):
        calls.append((start, end))
        return missing
    monkeypatch.setattr(history, "fetch_trade_candles", fetch)
    repaired = history.repair_history()
    assert calls == [(START + pd.Timedelta(minutes=5), START + pd.Timedelta(minutes=10))]
    assert list(repaired.close) == [100, 110, 120]
    assert not (repaired.timestamp.diff() > pd.Timedelta(minutes=5)).any()
    pd.testing.assert_frame_equal(repaired, pd.read_parquet(tmp_path / "XBTEUR_5m.parquet"))


def test_failed_download_preserves_file(tmp_path, monkeypatch):
    candles = history.candles_from_trades([trade(0, 100, 1, 1), trade(10, 120, 1, 2)], START, END, 5)
    path = tmp_path / "XBTEUR_5m.parquet"
    candles.to_parquet(path, index=False)
    original = path.read_bytes()
    monkeypatch.setattr(history, "DATA_DIR", tmp_path)
    monkeypatch.setattr(history, "load_ohlcv", lambda *a: candles)
    def interrupted(*args):
        raise RuntimeError("Connexion interrompue")
    monkeypatch.setattr(history, "fetch_trade_candles", interrupted)
    with pytest.raises(RuntimeError, match="Connexion"):
        history.repair_history()
    assert path.read_bytes() == original
