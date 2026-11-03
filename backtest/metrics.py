import pandas as pd

def calculate_max_drawdown(equity_curve):

    if not equity_curve:
        raise ValueError("Equity curve cannot be empty")

    if equity_curve[0] <= 0:
        raise ValueError("Initial equity must be positive")

    peak = 0
    drawdown = []
    max_drawdown = 0.0
    
    for equity in equity_curve:

        peak = max(peak, equity)
        
        dd = abs((equity - peak))/peak
        drawdown.append(dd)
        max_drawdown = max(max_drawdown, dd)
        


    return {
        "drawdown_curve": drawdown,
        "max_drawdown": max_drawdown
    }



def calculate_pnl(trades, current_price):

    realized_pnl = 0.0
    unrealized_pnl = 0.0
    pnl = 0.0
    entry_price = 0.0
    entry_fee = 0.0
    fee = 0.0

    for t in trades:
        side = t["side"]
        price = t["price"]
        quantity = t["quantity"]
        fee = t.get("fee", 0.0)

        if side == "BUY":
             entry_price = price 
             entry_quantity = quantity
             entry_fee = fee

        elif side == "SELL":
             realized_pnl += (price - entry_price) * quantity - entry_fee - fee  
             entry_price = 0

    if trades and trades[-1]["side"] == "BUY":
        unrealized_pnl = (current_price  - entry_price) * entry_quantity - entry_fee

    pnl = realized_pnl + unrealized_pnl   

    return {
        "realized_pnl": realized_pnl,
        "unrealized_pnl": unrealized_pnl,
        "total_pnl": pnl
    }


def calculate_total_return(initial_equity, final_equity):
    if initial_equity <= 0:
        raise ValueError("Initial equity must be positive")

    return (final_equity - initial_equity)/initial_equity


def calculate_win_rate(trades):

    
    entry_price = 0.0
    entry_fee = 0.0
    win_trades = 0
    total_trades = 0

    for t in trades:
        if t["side"] == "BUY":
            entry_price = t["price"]
            entry_fee = t.get("fee", 0.0)

        elif t["side"] == "SELL":
            total_trades += 1

            trade_pnl = (
                (t["price"] - entry_price) * t["quantity"]
                - entry_fee
                - t.get("fee", 0.0)
            )

            if trade_pnl > 0:
                win_trades += 1
                
    if total_trades == 0:
        return 0.0

    return win_trades/total_trades