
from pathlib import Path

import pandas as pd
import requests


BASE_URL = "https://api.kraken.com/0/public/OHLC"
DATA_DIR = Path(__file__).resolve().parent.parent / "market_data" / "kraken"


def get_ohlcv(symbol="XBTEUR", interval=5, format="pandas"):
    """
    Récupère les bougies OHLCV depuis Kraken.
    Sauvegarde les données en Parquet.
    Renvoie un DataFrame ou du JSON.
    """

    if format not in {"pandas", "json"}:
        raise ValueError("Format attendu : pandas ou json")

    # 1. Récupération depuis Kraken
    response = requests.get(
        BASE_URL,
        params={"pair": symbol, "interval": interval},
        timeout=15
    )
    response.raise_for_status()

    payload = response.json()

    if payload["error"]:
        raise RuntimeError(payload["error"])

    key = next(k for k in payload["result"] if k != "last")
    candles = payload["result"][key]
    

    # 2. Conversion en DataFrame
    df = pd.DataFrame(
        candles,
        columns=[
            "timestamp", "open", "high", "low",
            "close", "vwap", "volume", "count"
        ]
    )

    df["timestamp"] = pd.to_datetime(
        df["timestamp"], unit="s", utc=True
    )

    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col])

    # Retirer la bougie en cours
    df = df.iloc[:-1].copy()

    # 3. Sauvegarde locale
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / f"{symbol}_{interval}m.parquet"

    # Fusionner avec les anciennes données
    if path.exists():
        old = pd.read_parquet(path)
        df = pd.concat([old, df])

    df = (
        df.drop_duplicates(subset="timestamp", keep="last")
          .sort_values("timestamp")
          .reset_index(drop=True)
    )

    df = prepare_ohlcv(df)
    temporary = path.with_suffix(".tmp.parquet")
    df.to_parquet(temporary, index=False)
    temporary.replace(path)

    # 4. Retour
    if format == "json":
        return df.to_json(orient="records", date_format="iso")

    if format != "pandas":
        raise ValueError("Format attendu : pandas ou json")

    return df


def get_ticker(symbol="XBTEUR"):
    """Prix acheteur, vendeur et dernier échange pour la simulation."""
    response = requests.get(
        "https://api.kraken.com/0/public/Ticker",
        params={"pair": symbol}, timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    if payload["error"]:
        raise RuntimeError(payload["error"])
    ticker = next(iter(payload["result"].values()))
    return {"ask": float(ticker["a"][0]),
            "bid": float(ticker["b"][0]),
            "last": float(ticker["c"][0])}


def load_ohlcv(symbol="XBTEUR", interval=5):
    """Charge uniquement le Parquet local ; aucun appel réseau."""
    path = DATA_DIR / f"{symbol}_{interval}m.parquet"
    if not path.exists():
        raise ValueError("Pas de données locales. Cocher Actualiser depuis Kraken.")
    return prepare_ohlcv(pd.read_parquet(path))


def prepare_ohlcv(data):
    required = {"timestamp", "open", "high", "low", "close", "volume"}
    if not required.issubset(data.columns):
        raise ValueError("Colonnes OHLCV manquantes.")
    data = data.copy()
    data["timestamp"] = pd.to_datetime(data["timestamp"], utc=True).astype("datetime64[ns, UTC]")
    numeric = (required - {"timestamp"}) | ({"vwap", "count"} & set(data.columns))
    for column in numeric:
        data[column] = pd.to_numeric(data[column], errors="raise")
    if data.empty or data[list(required)].isna().any().any():
        raise ValueError("Données vides ou bougies incomplètes.")
    if (data[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("Les prix doivent être positifs.")
    return data.drop_duplicates("timestamp", keep="last").sort_values("timestamp").reset_index(drop=True)


def get_pair_rules(symbol="XBTEUR", refresh=False):
    """Snapshot des règles actuelles, réutilisé hors ligne pour les backtests."""
    import json
    from datetime import datetime, timezone

    path = DATA_DIR / f"{symbol}_rules.json"
    if path.exists() and not refresh:
        return json.loads(path.read_text())
    response = requests.get("https://api.kraken.com/0/public/AssetPairs",
                            params={"pair": symbol}, timeout=15)
    response.raise_for_status()
    payload = response.json()
    if payload["error"]:
        raise RuntimeError(payload["error"])
    pair = next(iter(payload["result"].values()))
    rules = {"symbol": symbol, "ordermin": pair["ordermin"],
             "costmin": pair["costmin"], "lot_decimals": pair["lot_decimals"],
             "fetched_at": datetime.now(timezone.utc).isoformat()}
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(rules, indent=2))
    temporary.replace(path)
    return rules
