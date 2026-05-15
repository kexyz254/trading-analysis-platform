"""Performance analysis for backtest results."""

import pandas as pd
import numpy as np

def calculate_annualized_return(returns, periods_per_year=252):
    """Calculate annualized return from daily returns."""
    total_return = (1 + returns).prod() - 1
    n_years = len(returns) / periods_per_year
    return (1 + total_return) ** (1 / n_years) - 1 if n_years > 0 else 0

def calculate_sharpe_ratio_annualized(returns, risk_free_rate=0.02, periods_per_year=252):
    """Calculate annualized Sharpe ratio."""
    excess_returns = returns - risk_free_rate / periods_per_year
    return excess_returns.mean() / excess_returns.std() * np.sqrt(periods_per_year)

def calculate_calmar_ratio(returns, max_drawdown, periods_per_year=252):
    """Calculate Calmar ratio."""
    annual_return = calculate_annualized_return(returns, periods_per_year)
    return annual_return / abs(max_drawdown) if max_drawdown != 0 else float('inf')

def calculate_win_rate(trades):
    """Calculate win rate from list of trades."""
    if not trades:
        return 0
    wins = sum(1 for t in trades if t > 0)
    return wins / len(trades)

def calculate_profit_factor(gross_profits, gross_losses):
    """Calculate profit factor."""
    return abs(gross_profits / gross_losses) if gross_losses != 0 else float('inf')

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
