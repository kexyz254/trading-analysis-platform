import pytest
import pandas as pd
import numpy as np
from trading_assistant.risk.advanced_metrics import AdvancedRiskMetrics, PortfolioRiskAggregator

def test_historical_var():
    returns = pd.Series(np.random.normal(0, 0.01, 1000))
    risk = AdvancedRiskMetrics(returns)
    var = risk.historical_var(0.95)
    assert var < 0

def test_maximum_drawdown():
    returns = pd.Series([0.01, -0.05, 0.02, -0.03, 0.01])
    risk = AdvancedRiskMetrics(returns)
    dd = risk.maximum_drawdown()
    assert dd < 0

def test_calmar_ratio():
    returns = pd.Series([0.01] * 252)
    risk = AdvancedRiskMetrics(returns)
    calmar = risk.calmar_ratio()
    assert calmar > 0

def test_portfolio_risk_aggregator():
    returns_df = pd.DataFrame({
        'AAPL': np.random.normal(0.001, 0.02, 500),
        'GOOGL': np.random.normal(0.001, 0.02, 500),
        'MSFT': np.random.normal(0.001, 0.02, 500)
    })
    agg = PortfolioRiskAggregator(returns_df)
    weights = np.array([0.4, 0.3, 0.3])
    var = agg.portfolio_var(weights)
    assert isinstance(var, float)