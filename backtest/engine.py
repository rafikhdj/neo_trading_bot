def backtest(strategy, data, capital=50, fee_rate=0.0, slippage_rate=0.0, position_size=0.10):
    """Exécute les signaux à l'ouverture suivante, avec frais et slippage.

    Les taux sont des fractions : 0.001 = 0,1 %. Une position utilise
    la fraction position_size du cash disponible, frais d'achat inclus.
    Pas de vente à découvert.
    """
    if data.empty:
        raise ValueError("Aucune bougie pour le backtest.")
    if capital <= 0 or not 0 < position_size <= 1:
        raise ValueError("Capital positif et taille de position entre 0 et 1 attendus.")
    if not 0 <= fee_rate < 1 or not 0 <= slippage_rate < 1:
        raise ValueError("Frais et slippage doivent être compris entre 0 inclus et 1 exclu.")
    initial_capital = capital
    quantity = 0
    trades = []
    signals = []
    nb_buy = 0
    nb_sell = 0
    equity_curve = [{
        "timestamp": data.iloc[0].get("timestamp", 0),
        "equity": capital,
    }]

    for i in range(len(data) - 1):
        signal = strategy(data.iloc[:i + 1])
        signals.append(signal)
        candle = data.iloc[i + 1]
        timestamp = candle.get("timestamp", i + 1)

        if signal == 1 and quantity == 0:
            price = candle["open"] * (1 + slippage_rate)
            budget = capital * position_size
            quantity = budget / (price * (1 + fee_rate))
            fee = quantity * price * fee_rate
            capital -= budget
            nb_buy += 1
            trades.append({
                "timestamp": timestamp,
                "side": "BUY",
                "price": price,
                "quantity": quantity,
                "fee": fee,
                "capital": capital,
            })

        elif signal == -1 and quantity > 0:
            price = candle["open"] * (1 - slippage_rate)
            proceeds = quantity * price
            fee = proceeds * fee_rate
            capital += proceeds - fee
            nb_sell += 1
            trades.append({
                "timestamp": timestamp,
                "side": "SELL",
                "price": price,
                "quantity": quantity,
                "fee": fee,
                "capital": capital,
            })
            quantity = 0

        equity_curve.append({
            "timestamp": timestamp,
            "equity": capital + quantity * candle["close"],
        })

    # Une position ouverte est valorisée au dernier close, sans vente fictive.
    final_capital = capital + quantity * data.iloc[-1]["close"]
    return {
        "capital_final": round(final_capital, 2),
        "pnl": round(final_capital - initial_capital, 2),
        "signals": signals,
        "trades": trades,
        "equity_curve": equity_curve,
        "total_fees": sum(trade["fee"] for trade in trades),
        "nb_buy": nb_buy,
        "nb_sell": nb_sell,
    }
