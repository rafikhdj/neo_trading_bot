"""Breakout Donchian horaire, calculé uniquement sur les clôtures connues."""
import pandas as pd

NAME = "Breakout horaire · tendance"
DESCRIPTION = (
    "Décision une fois par heure sur bougies complètes : achat au-dessus du plus haut "
    "des 20 heures précédentes si la moyenne 72 h monte ; sortie sous le plus bas "
    "des 10 heures précédentes ou sous la moyenne 72 h. Historique horaire incomplet : sortie."
)
ENTRY_HOURS = 20
EXIT_HOURS = 10
TREND_HOURS = 72
WARMUP = (TREND_HOURS + 1) * 12  # Données d'entrée : bougies 5 minutes.


def strategy(data):
    if len(data) < WARMUP:
        return 0
    timestamp = pd.Timestamp(data.iloc[-1]["timestamp"])
    if timestamp.minute != 55:
        return 0

    # Le timestamp Kraken désigne l'ouverture ; XX:55 clôture à l'heure suivante.
    # La dernière heure utilisée ne contient donc jamais une bougie future.
    recent = data.iloc[-(TREND_HOURS + 2) * 12:].set_index("timestamp")
    hourly = recent.resample("1h", origin="epoch").agg(
        high=("high", "max"), low=("low", "min"),
        close=("close", "last"), candles=("close", "count"),
    ).iloc[-(TREND_HOURS + 1):]
    if len(hourly) < TREND_HOURS + 1:
        return 0
    if (hourly["candles"] != 12).any():
        return -1  # Pas d'entrée sur un historique incomplet, sortie d'une position existante.

    close = hourly["close"].iloc[-1]
    trend = hourly["close"].iloc[-TREND_HOURS:].mean()
    previous_trend = hourly["close"].iloc[-TREND_HOURS - 1:-1].mean()
    prior = hourly.iloc[:-1]
    if close < prior["low"].iloc[-EXIT_HOURS:].min() or close < trend:
        return -1
    if (close > prior["high"].iloc[-ENTRY_HOURS:].max()
            and close > trend and trend > previous_trend):
        return 1
    return 0
