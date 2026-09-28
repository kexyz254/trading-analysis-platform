"""Missing held-asset prices must never become fictitious drawdown evidence."""

import pytest

from trading_assistant.agents.portfolio import PortfolioAgent
from trading_assistant.portfolio.manager import PortfolioManager


@pytest.mark.parametrize("missing_price", [None, 0.0, -1.0, float("nan"), float("inf")])
def test_held_position_requires_valid_current_price(missing_price):
    portfolio = PortfolioManager(initial_capital=1000.0)
    assert portfolio.open_position("BTC/USDT", price=100.0, quantity=1.0)
    assert portfolio.get_total_value({"BTC/USDT": 100.0}) == pytest.approx(1000.0)

    prices = {"ETH/USDT": 200.0}
    if missing_price is not None:
        prices["BTC/USDT"] = missing_price
    with pytest.raises(ValueError, match="Missing valid price for tracked position"):
        PortfolioAgent(portfolio).mark_to_market(prices)
    assert portfolio.equity_curve == [1000.0]


def test_untracked_reconciled_quantity_does_not_require_valuation():
    portfolio = PortfolioManager(initial_capital=1000.0)
    assert portfolio.open_position("BTC/USDT", price=100.0, quantity=1.0)
    portfolio.positions["BTC/USDT"]["unexplained_quantity"] = 1.0
    assert portfolio.get_total_value({}) == pytest.approx(900.0)
