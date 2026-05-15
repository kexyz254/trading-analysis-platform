"""Tests for portfolio management."""
import pytest
import pandas as pd
import numpy as np
from trading_assistant.portfolio.manager import PortfolioManager, RiskManager

def test_portfolio_manager_open_position():
    pm = PortfolioManager(10000)
    result = pm.open_position('AAPL', 100, 10)
    assert result is True
    assert pm.capital == 9000

def test_portfolio_manager_close_position():
    pm = PortfolioManager(10000)
    pm.open_position('AAPL', 100, 10)
    pnl = pm.close_position('AAPL', 110)
    assert pnl == 100
    assert pm.capital == 10100

def test_portfolio_manager_stop_loss():
    pm = PortfolioManager(10000)
    pm.open_position('AAPL', 100, 10, stop_loss=95)
    triggered = pm.check_stop_loss_take_profit('AAPL', 94)
    assert triggered is True
    assert 'AAPL' not in pm.positions

def test_risk_manager_position_size():
    rm = RiskManager(max_position_size=0.1)
    size = rm.calculate_position_size(10000, 0.02, 0.01)
    assert size > 0
    assert size <= 1000

def test_risk_manager_kelly():
    rm = RiskManager()
    kelly = rm.calculate_kelly_fraction(0.6, 2, 1)
    assert 0.2 < kelly < 0.3

def test_portfolio_manager_get_total_value():
    pm = PortfolioManager(10000)
    pm.open_position('AAPL', 100, 10)
    current_prices = {'AAPL': 105}
    total_value = pm.get_total_value(current_prices)
    assert abs(total_value - 10050) < 1

def test_portfolio_manager_get_unrealized_pnl():
    pm = PortfolioManager(10000)
    pm.open_position('AAPL', 100, 10)
    pnl = pm.get_position_pnl('AAPL', 105)
    assert pnl == 50

def test_portfolio_manager_performance_summary():
    pm = PortfolioManager(10000)
    pm.open_position('AAPL', 100, 10)
    pm.close_position('AAPL', 110)
    summary = pm.get_performance_summary()
    assert summary['total_trades'] == 1
    assert summary['total_pnl'] == 100

def test_portfolio_manager_max_drawdown():
    pm = PortfolioManager(10000)
    pm.equity_curve = [10000, 11000, 10500, 12000, 11500]
    max_dd = pm.get_max_drawdown()
    assert isinstance(max_dd, float)

def test_portfolio_manager_sharpe_ratio():
    pm = PortfolioManager(10000)
    pm.equity_curve = [10000, 10100, 10200, 10150, 10300]
    sharpe = pm.get_sharpe_ratio()
    assert isinstance(sharpe, float)

def test_portfolio_manager_calmar_ratio():
    pm = PortfolioManager(10000)
    pm.equity_curve = [10000, 11000, 10500, 12000, 11500]
    calmar = pm.get_calmar_ratio()
    assert isinstance(calmar, float)
