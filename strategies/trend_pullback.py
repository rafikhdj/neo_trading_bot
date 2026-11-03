"""Achat d'une reprise après repli, uniquement dans une tendance haussière."""
import pandas as pd

NAME = "Pullback tendance · 1 h"
DESCRIPTION = (
    "Clôtures horaires complètes : achat au retour au-dessus de la moyenne 20 h "
    "après un repli, si elle dépasse la moyenne 50 h et que cette dernière monte. "
    "Sortie sous la moyenne 50 h ou le plus bas des 6 heures précédentes."
)
WARMUP = 52 * 12


def strategy(data):
    if len(data) < WARMUP or pd.Timestamp(data.iloc[-1]["timestamp"]).minute != 55:
        return 0
    bars = data.iloc[-53 * 12:].set_index("timestamp").resample("1h", origin="epoch").agg(
        close=("close", "last"), low=("low", "min"), candles=("close", "count"),
    ).iloc[-52:]
    if len(bars) < 52:
        return 0
    if (bars.candles != 12).any():
        return -1
    close = bars.close
    fast, slow = close.iloc[-20:].mean(), close.iloc[-50:].mean()
    previous_fast = close.iloc[-21:-1].mean()
    previous_slow = close.iloc[-51:-1].mean()
    if close.iloc[-1] < slow or close.iloc[-1] < bars.low.iloc[-7:-1].min():
        return -1
    if (fast > slow and slow > previous_slow
            and close.iloc[-2] <= previous_fast and close.iloc[-1] > fast):
        return 1
    return 0
