"""Helper functions for trading calculations."""

import pandas as pd
import numpy as np

def calculate_returns(prices):
    """Calculate percentage returns from price series."""
    return prices.pct_change()

def calculate_log_returns(prices):
    """Calculate logarithmic returns."""
    return np.log(prices / prices.shift(1))

def calculate_volatility(returns, periods=252):
    """Calculate annualized volatility."""
    return returns.std() * np.sqrt(periods)

def calculate_max_drawdown(prices):
    """Calculate maximum drawdown from peak."""
    rolling_max = prices.expanding().max()
    drawdown = (prices - rolling_max) / rolling_max
    return drawdown.min()

def calculate_win_rate(trades):
    """Calculate win rate from list of trade returns."""
    wins = sum(1 for t in trades if t > 0)
    return wins / len(trades) if trades else 0

def calculate_profit_factor(gross_profits, gross_losses):
    """Calculate profit factor (gross profits / gross losses)."""
    return abs(gross_profits / gross_losses) if gross_losses != 0 else float('inf')

def calculate_calmar_ratio(returns, max_drawdown):
    """Calculate Calmar Ratio (return / max drawdown)."""
    return returns / abs(max_drawdown) if max_drawdown != 0 else float('inf')
