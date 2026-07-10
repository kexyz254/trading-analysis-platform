"""Pre-trade execution quality guard.

ExecutionAgent fires a real market order using a quantity computed from a
specific reference price (RiskAgent's position sizing), but a market order
placed during a moment of abnormally wide bid-ask spread / thin liquidity
can fill far worse than that reference price - silently making the actual
risk taken larger than what was sized. This checks the CURRENT spread
immediately before firing a real order, rejecting (not executing) if it's
too wide, rather than trusting whatever price the decision was made from
minutes or cycles earlier is still representative of fillable liquidity.
"""

from __future__ import annotations

from typing import Optional


def spread_pct(bid: Optional[float], ask: Optional[float]) -> Optional[float]:
    """Bid-ask spread as a fraction of the mid price, or None if bid/ask
    aren't usable (missing, non-positive, or crossed/inverted - a genuinely
    corrupt or stale quote, not a real market state)."""
    if bid is None or ask is None:
        return None
    if bid <= 0 or ask <= 0 or ask < bid:
        return None
    mid = (bid + ask) / 2
    return (ask - bid) / mid


def spread_too_wide(bid: Optional[float], ask: Optional[float], max_spread_pct: float) -> bool:
    """True only when a genuinely computable spread exceeds max_spread_pct.
    An unusable quote (spread_pct returns None) does NOT block the trade -
    a missing/corrupt quote is a data gap, not evidence the market is
    actually illiquid right now, and treating them the same would reject
    trades based on absence of information rather than an observed problem."""
    pct = spread_pct(bid, ask)
    if pct is None:
        return False
    return pct > max_spread_pct
