import pandas as pd

from data import kraken


def test_kraken_excludes_open_candle_and_merges(tmp_path, monkeypatch):
    monkeypatch.setattr(kraken, "DATA_DIR", tmp_path)
    candle = lambda t, c: [t, "100", "110", "90", str(c), "100", "2", 1]
    payloads = iter([
        {"error": [], "result": {"XXBTZEUR": [candle(300, 100), candle(600, 101), candle(900, 102)], "last": 600}},
        {"error": [], "result": {"XXBTZEUR": [candle(600, 103), candle(900, 104), candle(1200, 105)], "last": 900}},
    ])
    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return next(payloads)
    monkeypatch.setattr(kraken.requests, "get", lambda *a, **kw: Response())
    first = kraken.get_ohlcv()
    assert list(first.close) == [100, 101]
    second = kraken.get_ohlcv()
    assert list(second.close) == [100, 103, 104]
    pd.testing.assert_frame_equal(second, kraken.load_ohlcv())
