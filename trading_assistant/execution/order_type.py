"""Adaptive order placement: choose a marketable limit order over a plain
market order when the spread is comfortably tight, rather than always
using the same order type regardless of current conditions.

A market order accepts whatever price is available with no cap at all -
fine when the spread is already narrow (there's little room for a bad
fill anyway), but unnecessarily risky when it's wider: a sudden print
between the decision and the fill can move the price further than the
spread itself would suggest. A "marketable" limit order - priced
aggressively enough to fill immediately against current liquidity in
normal conditions - gets the same practical fill behavior in a tight
spread while capping the worst-case price the moment conditions turn
against the trade.
"""

from __future__ import annotations

from typing import Optional

DEFAULT_TIGHT_SPREAD_FRACTION = 0.3
DEFAULT_LIMIT_BUFFER_PCT = 0.001


def choose_order_type(
    spread_pct: Optional[float],
    max_spread_pct: Optional[float],
    tight_spread_fraction: float = DEFAULT_TIGHT_SPREAD_FRACTION,
) -> str:
    """'limit' when the spread is comfortably tight (at most
    tight_spread_fraction of max_spread_pct) - a marketable limit order
    caps the worst-case fill price without meaningfully risking a missed
    fill, since the spread is already narrow. 'market' otherwise (or when
    spread/threshold data isn't available - a data gap should default to
    the simpler, always-fills order type, not an untested new path)."""
    if max_spread_pct is None or spread_pct is None:
        return "market"
    if spread_pct <= max_spread_pct * tight_spread_fraction:
        return "limit"
    return "market"


def marketable_limit_price(current_price: float, side: str, buffer_pct: float = DEFAULT_LIMIT_BUFFER_PCT) -> float:
    """A limit price aggressive enough to fill immediately against
    current liquidity in normal conditions - a buy priced slightly ABOVE
    the last price, a sell priced slightly BELOW it - while still capping
    the worst plausible fill price, unlike an unbounded market order."""
    return current_price * (1 + buffer_pct) if side == "buy" else current_price * (1 - buffer_pct)
