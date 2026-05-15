"""Risk metrics for portfolio evaluation."""

import numpy as np
import pandas as pd

def value_at_risk(returns, confidence_level=0.95):
    """Calculate Value at Risk (VaR)."""
    return np.percentile(returns, (1 - confidence_level) * 100)

def conditional_var(returns, confidence_level=0.95):
    """Calculate Conditional VaR (Expected Shortfall)."""
    var = value_at_risk(returns, confidence_level)
    return returns[returns <= var].mean()

def sortino_ratio(returns, target_return=0, periods_per_year=252):
    """Calculate Sortino Ratio (downside risk only)."""
    downside_returns = returns[returns < target_return]
    downside_deviation = downside_returns.std()
    if downside_deviation == 0 or pd.isna(downside_deviation):
        return float('inf')
    excess_return = returns.mean() - target_return
    return excess_return / downside_deviation * np.sqrt(periods_per_year)

def beta(asset_returns, market_returns):
    """Calculate beta relative to market."""
    covariance = np.cov(asset_returns, market_returns)[0][1]
    market_variance = np.var(market_returns)
    return covariance / market_variance if market_variance != 0 else 1
