"""Position sizing calculations for risk management."""

import math

def fixed_fraction_sizing(capital, risk_per_trade=0.02):
    """Risk fixed percentage of capital per trade."""
    return capital * risk_per_trade

def kelly_criterion(win_probability, win_loss_ratio):
    """Calculate optimal position size using Kelly Criterion."""
    if win_loss_ratio <= 0:
        return 0
    kelly = win_probability - ((1 - win_probability) / win_loss_ratio)
    return max(0, min(kelly, 0.25))

def volatility_sizing(capital, atr, multiplier=2):
    """Size position based on ATR volatility."""
    risk_amount = capital * 0.02
    position_size = risk_amount / (atr * multiplier)
    return position_size

def percent_risk_sizing(capital, stop_loss_pct, risk_percent=0.02):
    """Size position based on stop loss percentage."""
    risk_amount = capital * risk_percent
    position_size = risk_amount / (stop_loss_pct / 100)
    return position_size

def equal_weight_sizing(num_positions, capital):
    """Equal weight across all positions."""
    return capital / num_positions if num_positions > 0 else 0
