"""Dependency-light portfolio performance metrics.

These helpers are imported by the dashboard and readiness layer. Keeping them
free of pandas and NumPy avoids loading the engine's analytical stack into a
small web process merely to calculate a few scalar statistics.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List


def max_drawdown_from_equity_curve(equity_curve: List[float]) -> float:
    if len(equity_curve) < 2:
        return 0.0
    peak = equity_curve[0]
    max_drawdown = 0.0
    for value in equity_curve:
        if value > peak:
            peak = value
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - value) / peak)
    return max_drawdown


def sharpe_ratio_from_equity_curve(
    equity_curve: List[float],
    risk_free_rate: float = 0.02,
    periods_per_year: float = 252,
) -> float:
    if len(equity_curve) < 2:
        return 0.0

    returns = [
        (current - previous) / previous
        for previous, current in zip(equity_curve, equity_curve[1:])
        if previous > 0
    ]
    if len(returns) < 2:
        return 0.0

    adjustment = risk_free_rate / periods_per_year
    excess_returns = [value - adjustment for value in returns]
    mean_return = sum(excess_returns) / len(excess_returns)
    variance = sum(
        (value - mean_return) ** 2 for value in excess_returns
    ) / len(excess_returns)
    standard_deviation = math.sqrt(variance)

    if standard_deviation < 1e-12:
        return 0.0
    return mean_return / standard_deviation * math.sqrt(periods_per_year)


def performance_summary_from_trade_pnls(trades_pnl: List[float]) -> Dict[str, Any]:
    if not trades_pnl:
        return {}

    winning_trades = [pnl for pnl in trades_pnl if pnl > 0]
    losing_trades = [pnl for pnl in trades_pnl if pnl < 0]

    return {
        "total_trades": len(trades_pnl),
        "winning_trades": len(winning_trades),
        "losing_trades": len(losing_trades),
        "win_rate": len(winning_trades) / len(trades_pnl),
        "total_pnl": sum(trades_pnl),
        "avg_win": (
            sum(winning_trades) / len(winning_trades)
            if winning_trades else 0
        ),
        "avg_loss": (
            sum(losing_trades) / len(losing_trades)
            if losing_trades else 0
        ),
        "profit_factor": (
            sum(winning_trades) / abs(sum(losing_trades))
            if losing_trades else None
        ),
    }
