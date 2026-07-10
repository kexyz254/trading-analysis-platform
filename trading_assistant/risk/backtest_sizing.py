"""Risk-based position sizing for walk-forward backtests and paper trading.

StrategyManager's tournament, LearningAgent's walk-forward validation, and
ChampionChallengerAgent's paper trading all previously sized every trade at
a flat ~95% of available capital, regardless of stop distance or risk
tolerance - a completely different sizing regime from RiskAgent.evaluate_buy,
which sizes real trades from risk_per_trade/stop_loss_pct (capped at
max_position_size, typically ~10% of capital by default). That mismatch
means a strategy's walk-forward-validated Sharpe/drawdown/win-rate describe
performance at a risk level the system would never actually trade at live -
this closes that gap so the SAME formula sizes both.
"""

from __future__ import annotations

from trading_assistant.portfolio.manager import RiskManager


def risk_based_quantity(
    capital: float,
    price: float,
    stop_loss_pct: float,
    risk_per_trade: float = 0.02,
    max_position_size: float = 0.1,
) -> float:
    """Asset quantity sized by the same risk_per_trade/stop-distance formula
    RiskAgent.evaluate_buy uses live (delegates to
    RiskManager.calculate_position_size, the single source of truth for
    that formula, rather than re-deriving it here)."""
    if capital <= 0 or price <= 0 or stop_loss_pct <= 0:
        return 0.0
    notional = RiskManager(max_position_size=max_position_size).calculate_position_size(
        capital, risk_per_trade=risk_per_trade, stop_loss_pct=stop_loss_pct
    )
    return notional / price
