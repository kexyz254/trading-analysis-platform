import pandas as pd
import pytest

from trading_assistant.portfolio import plan_portfolio_rebalance
from trading_assistant.portfolio.rebalance import PortfolioRebalanceError


def sample_positions():
    return pd.DataFrame(
        {
            "symbol": ["AAPL", "MSFT", "TSLA"],
            "quantity": [10, 5, 2],
            "price": [200.0, 100.0, 250.0],
        }
    )


def trades_by_symbol(plan):
    return {trade.symbol: trade for trade in plan.trades}


def test_plan_portfolio_rebalance_creates_buy_and_sell_recommendations():
    plan = plan_portfolio_rebalance(
        sample_positions(),
        {"AAPL": 0.40, "MSFT": 0.40, "TSLA": 0.20},
        tolerance=0.001,
    )

    trades = trades_by_symbol(plan)

    assert plan.total_value == 3000.0
    assert trades["AAPL"].action == "sell"
    assert trades["MSFT"].action == "buy"
    assert trades["TSLA"].action == "buy"
    assert trades["AAPL"].trade_value == -800.0
    assert trades["MSFT"].trade_value == 700.0
    assert trades["TSLA"].trade_value == 100.0


def test_plan_portfolio_rebalance_normalizes_percentage_targets():
    plan = plan_portfolio_rebalance(
        sample_positions(),
        {"AAPL": 40, "MSFT": 40, "TSLA": 20},
        tolerance=0.001,
    )

    trades = trades_by_symbol(plan)

    assert round(trades["MSFT"].target_weight, 2) == 0.40
    assert round(trades["TSLA"].target_weight, 2) == 0.20


def test_rebalance_plan_turnover_and_to_dict_are_json_friendly():
    plan = plan_portfolio_rebalance(
        sample_positions(),
        {"AAPL": 0.40, "MSFT": 0.40, "TSLA": 0.20},
        tolerance=0.001,
    )

    payload = plan.to_dict()

    assert plan.trade_count == 3
    assert round(plan.total_turnover, 4) == round(1600.0 / 3000.0, 4)
    assert payload["trade_count"] == 3
    assert isinstance(payload["trades"], list)
    assert payload["trades"][0]["symbol"] in {"AAPL", "MSFT", "TSLA"}


def test_plan_portfolio_rebalance_respects_tolerance():
    plan = plan_portfolio_rebalance(
        sample_positions(),
        {"AAPL": 0.67, "MSFT": 0.16, "TSLA": 0.17},
        tolerance=0.02,
    )

    assert plan.trade_count == 0


def test_plan_portfolio_rebalance_respects_min_trade_value():
    plan = plan_portfolio_rebalance(
        sample_positions(),
        {"AAPL": 0.40, "MSFT": 0.40, "TSLA": 0.20},
        tolerance=0.001,
        min_trade_value=150.0,
    )

    trades = trades_by_symbol(plan)

    assert "AAPL" in trades
    assert "MSFT" in trades
    assert "TSLA" not in trades


def test_plan_portfolio_rebalance_supports_custom_columns():
    positions = pd.DataFrame(
        {
            "ticker": ["AAPL", "MSFT"],
            "shares": [10, 5],
            "mark": [100.0, 200.0],
        }
    )

    plan = plan_portfolio_rebalance(
        positions,
        {"AAPL": 0.5, "MSFT": 0.5},
        symbol_column="ticker",
        quantity_column="shares",
        price_column="mark",
        tolerance=0.001,
    )

    assert plan.trade_count == 0


def test_plan_portfolio_rebalance_rejects_missing_columns():
    positions = pd.DataFrame({"symbol": ["AAPL"], "quantity": [10]})

    with pytest.raises(PortfolioRebalanceError, match="missing required"):
        plan_portfolio_rebalance(positions, {"AAPL": 1.0})


def test_plan_portfolio_rebalance_rejects_non_numeric_positions():
    positions = sample_positions().astype({"quantity": "object"})
    positions.loc[0, "quantity"] = "ten"

    with pytest.raises(PortfolioRebalanceError, match="numeric"):
        plan_portfolio_rebalance(positions, {"AAPL": 1.0})


def test_plan_portfolio_rebalance_rejects_short_positions():
    positions = sample_positions()
    positions.loc[0, "quantity"] = -1

    with pytest.raises(PortfolioRebalanceError, match="long positions"):
        plan_portfolio_rebalance(positions, {"AAPL": 1.0})


def test_plan_portfolio_rebalance_rejects_negative_prices():
    positions = sample_positions()
    positions.loc[0, "price"] = -1

    with pytest.raises(PortfolioRebalanceError, match="negative"):
        plan_portfolio_rebalance(positions, {"AAPL": 1.0})


def test_plan_portfolio_rebalance_rejects_invalid_target_weights():
    with pytest.raises(PortfolioRebalanceError, match="target weights"):
        plan_portfolio_rebalance(sample_positions(), {"AAPL": -1.0})


def test_plan_portfolio_rebalance_rejects_missing_price_for_new_buy():
    positions = pd.DataFrame(
        {
            "symbol": ["AAPL"],
            "quantity": [10],
            "price": [100.0],
        }
    )

    with pytest.raises(PortfolioRebalanceError, match="no current price"):
        plan_portfolio_rebalance(
            positions,
            {"AAPL": 0.5, "MSFT": 0.5},
            tolerance=0.001,
        )
