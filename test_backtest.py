
import pandas as pd
from backtest.engine import backtest


def make_data(prices):
    return pd.DataFrame({
        "open": prices,
        "high": prices,
        "low": prices,
        "close": prices
    })


def run_test(name, data, strategy, expected):
    result = backtest(strategy, data, capital=50)

    assert abs(result["capital_final"] - expected) < 1e-9, (
        f"{name} : attendu {expected}, "
        f"obtenu {result['capital_final']}"
    )

    print(f"{name} : OK | Capital final = {result['capital_final']} €")


# TEST 1 : trade gagnant
# Achat à 100, vente à 110
def winning_strategy(history):
    return 1 if len(history) == 1 else -1


run_test(
    "Trade gagnant",
    make_data([100, 100, 110, 110]),
    winning_strategy,
    50.50
)


# TEST 2 : trade perdant
# Achat à 100, vente à 90
run_test(
    "Trade perdant",
    make_data([100, 100, 90, 90]),
    winning_strategy,
    49.50
)


# TEST 3 : position encore ouverte
# Achat à 100, valorisation finale à 120
def hold_strategy(history):
    return 1


run_test(
    "Position ouverte",
    make_data([100, 100, 120, 120]),
    hold_strategy,
    51.00
)


# TEST 4 : aucun trade
def no_trade_strategy(history):
    return 0


run_test(
    "Aucun trade",
    make_data([100, 100, 110, 120]),
    no_trade_strategy,
    50.00
)


print("\nTOUS LES TESTS SONT RÉUSSIS")

# TEST 5 : frais de transaction
result = backtest(
    strategy=winning_strategy,
    data=make_data([100, 100, 110, 110]),
    capital=50,
    fee_rate=0.0025
)

assert result["capital_final"] == 50.47

print("Test frais : OK")


# TEST 6 : cohérence de l'historique des trades
result = backtest(
    strategy=winning_strategy,
    data=make_data([100, 100, 110, 110]),
    capital=50,
    fee_rate=0
)

assert len(result["trades"]) == result["nb_buy"] + result["nb_sell"]
assert len(result["trades"]) == 2
assert result["trades"][0]["side"] == "BUY"
assert result["trades"][1]["side"] == "SELL"

print("Test historique trades : OK")


def test_equity_history_and_initial_capital():
    result = backtest(winning_strategy, make_data([100, 100, 110, 110]), capital=200)
    assert result["capital_final"] == 202
    assert result["pnl"] == 2
    assert [point["equity"] for point in result["equity_curve"]] == [200, 200, 202, 202]
    assert result["signals"] == [1, -1, -1]


def test_execution_costs_and_pnl_agree():
    import pytest
    from backtest.metrics import calculate_pnl

    data = make_data([100, 100, 110, 110])
    result = backtest(winning_strategy, data, capital=200,
                      fee_rate=0.0025, slippage_rate=0.001)
    buy, sell = result["trades"]
    assert buy["price"] == pytest.approx(100.1)
    assert sell["price"] == pytest.approx(109.89)
    assert buy["quantity"] * buy["price"] + buy["fee"] == pytest.approx(20)
    final_equity = result["equity_curve"][-1]["equity"]
    pnl = calculate_pnl(result["trades"], current_price=110)
    assert pnl["total_pnl"] == pytest.approx(final_equity - 200)
    assert final_equity < 202
    assert result["total_fees"] == pytest.approx(buy["fee"] + sell["fee"])


def test_open_position_pnl_with_fees():
    import pytest
    from backtest.metrics import calculate_pnl

    result = backtest(hold_strategy, make_data([100, 100, 120]),
                      fee_rate=0.0025, slippage_rate=0.001)
    pnl = calculate_pnl(result["trades"], current_price=120)
    assert pnl["total_pnl"] == pytest.approx(result["equity_curve"][-1]["equity"] - 50)
    assert result["nb_buy"] == 1
    assert result["nb_sell"] == 0


def test_signal_uses_history_and_executes_next_open():
    import pytest
    data = make_data([100, 120, 90, 110])
    seen = []
    def decision(history):
        seen.append(list(history["close"]))
        return 1 if len(history) == 1 else -1
    result = backtest(decision, data)
    assert seen == [[100], [100, 120], [100, 120, 90]]
    assert result["trades"][0]["price"] == 120
    assert result["trades"][1]["price"] == 90
    assert result["capital_final"] == pytest.approx(48.75)
