# trading_assistant/evaluation/metrics.py
"""
Standardised performance metrics for strategy candidates.
All functions are pure and testable – no database or I/O.
"""

from typing import List, Dict, Optional
import numpy as np
import pandas as pd

def compute_metrics_from_trades(
    trades: List[Dict],
    initial_capital: float = 10000.0,
    annualization_factor: float = 252 * 24  # hourly bars for crypto
) -> Dict:
    """
    Compute a standard set of performance metrics from a list of trade dictionaries.
    Each trade dict must have:
        - 'pnl': profit/loss (float)
        - 'entry_time': timestamp (datetime or pandas Timestamp)
        - 'exit_time': timestamp
    Optional fields: 'regime' for regime-specific metrics.
    """
    if not trades:
        return {
            "net_pnl": 0.0,
            "profit_factor": 0.0,
            "sharpe": 0.0,
            "max_drawdown": 0.0,
            "win_rate": 0.0,
            "num_trades": 0,
            "avg_trade_duration": 0.0,
        }

    pnls = np.array([t['pnl'] for t in trades])
    net_pnl = pnls.sum()
    gross_profit = pnls[pnls > 0].sum()
    gross_loss = abs(pnls[pnls < 0].sum()) if np.any(pnls < 0) else 1e-10
    profit_factor = gross_profit / gross_loss
    win_rate = np.mean(pnls > 0)

    # Sharpe ratio: compute trade returns (not equity curve) – might be less accurate,
    # but suitable for small trade counts. We'll use the trade-level PnL as a proxy return.
    # More precisely, we can compute periodic returns from the equity curve if available.
    # Here we assume each trade's return is pnl / initial_capital (approximate).
    returns = pnls / initial_capital
    # annualized Sharpe
    sharpe = (returns.mean() / returns.std()) * np.sqrt(len(returns)) if returns.std() != 0 else 0.0

    # Max drawdown from cumulative PnL
    cumulative = np.cumsum(pnls)
    peak = np.maximum.accumulate(cumulative)
    drawdown = peak - cumulative
    max_drawdown = drawdown.max() if len(drawdown) > 0 else 0.0

    # Average trade duration
    durations = []
    for t in trades:
        dur = (pd.Timestamp(t['exit_time']) - pd.Timestamp(t['entry_time'])).total_seconds() / 3600.0  # hours
        durations.append(dur)
    avg_duration = np.mean(durations) if durations else 0.0

    return {
        "net_pnl": net_pnl,
        "profit_factor": profit_factor,
        "sharpe": sharpe,
        "max_drawdown": max_drawdown,
        "win_rate": win_rate,
        "num_trades": len(trades),
        "avg_trade_duration": avg_duration,
    }


def compute_metrics_by_regime(
    trades: List[Dict],
    initial_capital: float = 10000.0
) -> Dict[str, Dict]:
    """
    Split trades by regime label and compute metrics for each regime.
    """
    regimes = {}
    for t in trades:
        regime = t.get('regime', 'unknown')
        regimes.setdefault(regime, []).append(t)

    result = {}
    for regime, regime_trades in regimes.items():
        result[regime] = compute_metrics_from_trades(regime_trades, initial_capital)
    return result