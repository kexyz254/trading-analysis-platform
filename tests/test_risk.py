"""Tests for risk management modules."""
import pytest
import pandas as pd
import numpy as np
from trading_assistant.risk import *

def test_fixed_fraction_sizing():
    result = fixed_fraction_sizing(10000, 0.02)
    assert result == 200

def test_kelly_criterion():
    result = kelly_criterion(0.6, 1.5)
    assert 0.2 < result < 0.3

def test_volatility_sizing():
    result = volatility_sizing(10000, 50, 2)
    assert result > 0

def test_percent_risk_sizing():
    result = percent_risk_sizing(10000, 2, 0.02)
    assert result == 10000

def test_equal_weight_sizing():
    result = equal_weight_sizing(5, 10000)
    assert result == 2000

def test_value_at_risk():
    returns = pd.Series(np.random.normal(0, 0.01, 1000))
    var = value_at_risk(returns, 0.95)
    assert var < 0

def test_sortino_ratio():
    returns = pd.Series([0.01, -0.005, 0.02, -0.01, 0.015])
    ratio = sortino_ratio(returns)
    assert isinstance(ratio, float)

def test_beta_calculation():
    asset = pd.Series([0.01, 0.02, -0.01, 0.015, -0.005])
    market = pd.Series([0.008, 0.015, -0.005, 0.01, -0.002])
    b = beta(asset, market)
    assert b > 0
