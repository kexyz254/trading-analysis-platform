"""Tests for helper functions."""
import pytest
import pandas as pd
import numpy as np
from trading_assistant.utils.helpers import *

def test_calculate_returns():
    prices = pd.Series([100, 105, 102, 108])
    returns = calculate_returns(prices)
    assert abs(returns.iloc[1] - 0.05) < 0.01

def test_calculate_max_drawdown():
    prices = pd.Series([100, 110, 95, 105])
    drawdown = calculate_max_drawdown(prices)
    assert drawdown == -0.13636363636363635

def test_calculate_win_rate():
    trades = [100, -50, 200, -30, 50]
    win_rate = calculate_win_rate(trades)
    assert win_rate == 0.6

def test_calculate_profit_factor():
    result = calculate_profit_factor(500, 200)
    assert result == 2.5
