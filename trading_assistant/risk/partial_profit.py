"""Partial profit-taking: once a position has moved favorably enough, lock
in gains on PART of it while letting the remainder keep riding (protected
by whatever stop - trailing or fixed - is already in place on it).

Before this, the only exit was all-or-nothing: a single SELL signal closed
a position completely. That means either taking profit on the WHOLE
position the moment a signal fires (giving up any further upside) or
holding out for a full exit that may never come, giving back an
already-real gain if the market reverses before a SELL signal happens to
fire. Selling a portion once profit crosses a threshold locks in something
concrete without fully exiting a position that's still working.
"""

from __future__ import annotations

from typing import Optional


def unrealized_pnl_pct(entry_price: float, current_price: float) -> float:
    if entry_price <= 0:
        return 0.0
    return (current_price - entry_price) / entry_price


def should_take_partial_profit(
    entry_price: float,
    current_price: float,
    partial_profit_pct: Optional[float],
    already_taken: bool,
) -> bool:
    """`partial_profit_pct=None` disables the feature entirely (opt-in).
    `already_taken=True` means this position already had its partial exit
    - it only fires ONCE per position lifecycle, not every cycle for as
    long as price stays above the threshold."""
    if partial_profit_pct is None or already_taken:
        return False
    return unrealized_pnl_pct(entry_price, current_price) >= partial_profit_pct
