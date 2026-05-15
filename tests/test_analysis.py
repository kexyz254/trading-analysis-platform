"""Tests for performance analysis module."""
import pytest
import pandas as pd
import numpy as np
from trading_assistant.analysis.performance import *

def test_calculate_annualized_return():
    returns = pd.Series([0.01] * 252)
    result = calculate_annualized_return(returns)
    assert result > 0.01

def test_calculate_sharpe_ratio_annualized():
    returns = pd.Series([0.01] * 100)
    result = calculate_sharpe_ratio_annualized(returns)
    assert isinstance(result, float)

def test_calculate_win_rate():
    trades = [100, -50, 200, -30, 50]
    result = calculate_win_rate(trades)
    assert result == 0.6

def test_calculate_profit_factor():
    result = calculate_profit_factor(500, 200)
    assert result == 2.5

def test_calculate_expectancy():
    trades = [100, -50, 200, -30, 50]
    result = calculate_expectancy(trades)
    assert result == 54

def test_calculate_avg_win():
    trades = [100, -50, 200, -30, 50]
    result = calculate_avg_win(trades)
    assert result == 116.66666666666667

def test_calculate_avg_loss():
    trades = [100, -50, 200, -30, 50]
    result = calculate_avg_loss(trades)
    assert result == -40
