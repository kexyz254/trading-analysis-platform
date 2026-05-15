"""Backtest performance metrics."""

import pandas as pd
import numpy as np

def calculate_total_return(equity_curve):
    """Calculate total return percentage."""
    return (equity_curve.iloc[-1] - equity_curve.iloc[0]) / equity_curve.iloc[0]

def calculate_annualized_return(equity_curve, periods_per_year=252):
    """Calculate annualized return."""
    total_return = calculate_total_return(equity_curve)
    years = len(equity_curve) / periods_per_year
    return (1 + total_return) ** (1 / years) - 1 if years > 0 else 0

def calculate_max_drawdown(equity_curve):
    """Calculate maximum drawdown."""
    rolling_max = equity_curve.expanding().max()
    drawdown = (equity_curve - rolling_max) / rolling_max
    return drawdown.min()

def calculate_sharpe_ratio(returns, risk_free_rate=0.02, periods_per_year=252):
    """Calculate Sharpe ratio."""
    excess_returns = returns - risk_free_rate / periods_per_year
    return excess_returns.mean() / excess_returns.std() * np.sqrt(periods_per_year)

def calculate_sortino_ratio(returns, risk_free_rate=0.02, periods_per_year=252):
    """Calculate Sortino ratio (downside risk only)."""
    excess_returns = returns - risk_free_rate / periods_per_year
    downside_returns = excess_returns[excess_returns < 0]
    downside_deviation = downside_returns.std()
    if downside_deviation == 0:
        return float('inf')
    return excess_returns.mean() / downside_deviation * np.sqrt(periods_per_year)

def calculate_calmar_ratio(equity_curve, periods_per_year=252):
    """Calculate Calmar ratio."""
    annual_return = calculate_annualized_return(equity_curve, periods_per_year)
    max_dd = calculate_max_drawdown(equity_curve)
    return annual_return / abs(max_dd) if max_dd != 0 else float('inf')

def calculate_win_rate(trades):
    """Calculate win rate from trades."""
    if not trades:
        return 0
    wins = sum(1 for t in trades if t > 0)
    return wins / len(trades)

def calculate_profit_factor(trades):
    """Calculate profit factor."""
    gross_profit = sum(t for t in trades if t > 0)
    gross_loss = abs(sum(t for t in trades if t < 0))
    return gross_profit / gross_loss if gross_loss != 0 else float('inf')

def calculate_expectancy(trades):
    """Calculate average profit per trade."""
    if not trades:
        return 0
    return np.mean(trades)

def calculate_avg_win(trades):
    """Calculate average winning trade."""
    wins = [t for t in trades if t > 0]
    return np.mean(wins) if wins else 0

def calculate_avg_loss(trades):
    """Calculate average losing trade."""
    losses = [t for t in trades if t < 0]
    return np.mean(losses) if losses else 0

def calculate_payoff_ratio(trades):
    """Calculate payoff ratio."""
    avg_win = calculate_avg_win(trades)
    avg_loss = abs(calculate_avg_loss(trades))
    return avg_win / avg_loss if avg_loss != 0 else float('inf')

def calculate_recovery_factor(equity_curve, max_drawdown):
    """Calculate recovery factor."""
    total_gain = equity_curve.iloc[-1] - equity_curve.iloc[0]
    return total_gain / abs(max_drawdown) if max_drawdown != 0 else float('inf')
