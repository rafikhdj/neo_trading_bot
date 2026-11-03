from numbers import Real

import pytest

from backtest.metrics import (
    calculate_max_drawdown,
    calculate_pnl,
    calculate_total_return,
    calculate_win_rate,
)


@pytest.mark.parametrize(
    "trades, expected",
    [
        pytest.param(
            [
                {"side": "BUY", "price": 100, "quantity": 2, "fee": 1},
                {"side": "SELL", "price": 110, "quantity": 2, "fee": 1},
                {"side": "BUY", "price": 100, "quantity": 1, "fee": 1},
                {"side": "SELL", "price": 90, "quantity": 1, "fee": 1},
                {"side": "BUY", "price": 100, "quantity": 1.5, "fee": 1},
                {"side": "SELL", "price": 120, "quantity": 1.5, "fee": 1},
            ],
            2 / 3,
            id="two-winners-one-loser",
        ),
        pytest.param(
            [
                {"side": "BUY", "price": 100, "quantity": 1, "fee": 1},
                {"side": "SELL", "price": 110, "quantity": 1, "fee": 1},
                {"side": "BUY", "price": 120, "quantity": 2, "fee": 1},
                {"side": "SELL", "price": 130, "quantity": 2, "fee": 1},
            ],
            1.0,
            id="all-profitable",
        ),
        pytest.param(
            [
                {"side": "BUY", "price": 100, "quantity": 1, "fee": 1},
                {"side": "SELL", "price": 90, "quantity": 1, "fee": 1},
                {"side": "BUY", "price": 120, "quantity": 2, "fee": 1},
                {"side": "SELL", "price": 110, "quantity": 2, "fee": 1},
            ],
            0.0,
            id="all-losing",
        ),
        pytest.param(
            [
                {"side": "BUY", "price": 100, "quantity": 1, "fee": 1},
                {"side": "SELL", "price": 102, "quantity": 1, "fee": 1},
            ],
            0.0,
            id="break-even-after-fees",
        ),
        pytest.param(
            [
                {"side": "BUY", "price": 100, "quantity": 1, "fee": 1},
                {"side": "SELL", "price": 101, "quantity": 1, "fee": 1},
            ],
            0.0,
            id="gross-profit-net-loss",
        ),
        pytest.param(
            [{"side": "BUY", "price": 100, "quantity": 1, "fee": 1}],
            0.0,
            id="open-position-only",
        ),
        pytest.param([], 0.0, id="empty-history"),
        pytest.param(
            [
                {"side": "BUY", "price": 100, "quantity": 1, "fee": 1},
                {"side": "SELL", "price": 110, "quantity": 1, "fee": 1},
                {"side": "BUY", "price": 120, "quantity": 2, "fee": 1},
                {"side": "SELL", "price": 110, "quantity": 2, "fee": 1},
                {"side": "BUY", "price": 90, "quantity": 1.5, "fee": 1},
            ],
            0.5,
            id="open-position-excluded-from-win-rate",
        ),
    ],
)
def test_calculate_win_rate(trades, expected):
    result = calculate_win_rate(trades)

    assert result == pytest.approx(expected)


@pytest.mark.parametrize(
    "initial_equity, final_equity, expected",
    [
        pytest.param(100, 110, 0.10, id="positive-return"),
        pytest.param(100, 90, -0.10, id="negative-return"),
        pytest.param(100, 100, 0.0, id="no-change"),
        pytest.param(100, 0, -1.0, id="total-loss"),
    ],
)
def test_calculate_total_return(initial_equity, final_equity, expected):
    result = calculate_total_return(initial_equity, final_equity)

    assert result == pytest.approx(expected)


@pytest.mark.parametrize("initial_equity", [0, -100])
def test_total_return_invalid_initial_equity(initial_equity):
    with pytest.raises(ValueError):
        calculate_total_return(initial_equity, final_equity=100)


@pytest.mark.parametrize(
    "trades, current_price, expected",
    [
        pytest.param(
            [
                {"side": "BUY", "price": 100, "quantity": 2, "fee": 1.0},
                {"side": "SELL", "price": 120, "quantity": 2, "fee": 1.2},
            ],
            120,
            {"realized_pnl": 37.8, "unrealized_pnl": 0.0, "total_pnl": 37.8},
            id="profitable-closed-trade-with-fees",
        ),
        pytest.param(
            [{"side": "BUY", "price": 100, "quantity": 2, "fee": 1.0}],
            110,
            {"realized_pnl": 0.0, "unrealized_pnl": 19.0, "total_pnl": 19.0},
            id="open-position-with-buy-fee",
        ),
        pytest.param(
            [
                {"side": "BUY", "price": 100, "quantity": 2, "fee": 1.0},
                {"side": "SELL", "price": 110, "quantity": 2, "fee": 1.0},
                {"side": "BUY", "price": 120, "quantity": 1, "fee": 0.5},
                {"side": "SELL", "price": 115, "quantity": 1, "fee": 0.5},
                {"side": "BUY", "price": 90, "quantity": 1.5, "fee": 0.3},
            ],
            100,
            {"realized_pnl": 12.0, "unrealized_pnl": 14.7, "total_pnl": 26.7},
            id="completed-trades-and-open-position-with-fees",
        ),
        pytest.param(
            [],
            100,
            {"realized_pnl": 0.0, "unrealized_pnl": 0.0, "total_pnl": 0.0},
            id="no-trades",
        ),
    ],
)
def test_calculate_pnl_with_fees(trades, current_price, expected):
    # Quantities are those recorded in the trades; fees are separate amounts.
    result = calculate_pnl(trades, current_price)

    assert result == pytest.approx(expected)


@pytest.mark.parametrize(
    "equity, expected_drawdown, expected_max_drawdown",
    [
        pytest.param(
            [100, 90, 80, 85],
            [0, 0.1, 0.2, 0.15],
            0.2,
            id="normal-drawdown-and-partial-recovery",
        ),
        pytest.param(
            [100, 80, 100, 120, 90, 130],
            [0, 0.2, 0, 0, 0.25, 0],
            0.25,
            id="recovery-and-new-peaks",
        ),
        pytest.param(
            [100, 100, 100], [0, 0, 0], 0.0, id="constant-equity"
        ),
        pytest.param(
            [100, 110, 120], [0, 0, 0], 0.0, id="increasing-equity"
        ),
        pytest.param([100], [0], 0.0, id="single-value"),
        pytest.param(
            [100, 0, 50], [0, 1, 0.5], 1.0, id="total-loss-and-recovery"
        ),
    ],
)
def test_calculate_max_drawdown(equity, expected_drawdown, expected_max_drawdown):
    result = calculate_max_drawdown(equity)

    assert len(result["drawdown_curve"]) == len(equity)
    assert isinstance(result["max_drawdown"], Real)
    assert result["drawdown_curve"] == pytest.approx(expected_drawdown)
    assert result["max_drawdown"] == pytest.approx(expected_max_drawdown)


@pytest.mark.parametrize(
    "equity",
    [
        pytest.param([], id="empty-input"),
        pytest.param([0], id="zero-initial-equity"),
        pytest.param([0, 100, 80], id="zero-initial-before-positive-equity"),
        pytest.param([-100], id="negative-initial-equity"),
        pytest.param([100, -10, 80], id="negative-intermediate-equity"),
        pytest.param([100, 0, -10], id="negative-equity-after-zero"),
        pytest.param([float("nan"), 100], id="nan-initial-equity"),
        pytest.param([100, float("nan"), 80], id="nan-intermediate-equity"),
        pytest.param([100, float("nan")], id="nan-final-equity"),
        pytest.param([float("inf"), 100], id="positive-infinite-initial-equity"),
        pytest.param([float("-inf"), 100], id="negative-infinite-initial-equity"),
        pytest.param([100, float("inf"), 80], id="positive-infinite-intermediate-equity"),
        pytest.param([100, float("-inf"), 80], id="negative-infinite-intermediate-equity"),
        pytest.param([100, float("inf")], id="positive-infinite-final-equity"),
        pytest.param([100, float("-inf")], id="negative-infinite-final-equity"),
    ],
)
def test_invalid_equity_raises_value_error(equity):
    with pytest.raises(ValueError):
        calculate_max_drawdown(equity)


def test_pnl_no_trades():
    result = calculate_pnl([], current_price=100)

    assert result == pytest.approx({
        "realized_pnl": 0.0,
        "unrealized_pnl": 0.0,
        "total_pnl": 0.0,
    })


def test_pnl_closed_profitable_trade():
    trades = [
        {"side": "BUY", "price": 100, "quantity": 2},
        {"side": "SELL", "price": 110, "quantity": 2},
    ]

    result = calculate_pnl(trades, current_price=120)

    assert result == pytest.approx({
        "realized_pnl": 20.0,
        "unrealized_pnl": 0.0,
        "total_pnl": 20.0,
    })


def test_pnl_open_position():
    trades = [{"side": "BUY", "price": 100, "quantity": 1.5}]

    result = calculate_pnl(trades, current_price=110)

    assert result == pytest.approx({
        "realized_pnl": 0.0,
        "unrealized_pnl": 15.0,
        "total_pnl": 15.0,
    })


def test_pnl_completed_trades_followed_by_open_position():
    trades = [
        {"side": "BUY", "price": 100, "quantity": 2},
        {"side": "SELL", "price": 110, "quantity": 2},
        {"side": "BUY", "price": 120, "quantity": 3},
        {"side": "SELL", "price": 115, "quantity": 3},
        {"side": "BUY", "price": 90, "quantity": 1.5},
    ]

    result = calculate_pnl(trades, current_price=100)

    # Closed trades: 20 - 15 = 5. Open position: (100 - 90) * 1.5 = 15.
    assert result == pytest.approx({
        "realized_pnl": 5.0,
        "unrealized_pnl": 15.0,
        "total_pnl": 20.0,
    })
