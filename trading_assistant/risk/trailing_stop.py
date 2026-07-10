"""Trailing stop-loss: once a position has moved favorably, its protective
stop should follow price up (locking in gained ground) rather than sitting
frozen at the original entry-based level for the position's entire life.
Before this, every real position's protective stop was set once, at entry,
and never moved - a position that ran up 30% before reversing would give
back the whole move down to the ORIGINAL stop level, with nothing locking
in any of the gain along the way.

Only ever moves UP for a long position, never down - a trailing stop that
could retreat would give back exactly the protection it exists to lock in.
"""

from __future__ import annotations

from typing import Optional

# The default minimum improvement (as a fraction of the current resting
# stop price) required before actually moving the stop - an exchange
# cancel+replace round trip costs two API calls and a brief window where
# the position has no resting protection at all, so a fractional-percent
# improvement isn't worth chasing every single cycle.
DEFAULT_MIN_IMPROVEMENT_PCT = 0.001


def update_high_water_mark(high_water_mark: Optional[float], current_price: float) -> float:
    """The best price seen for this position since entry (or since the
    last time it was tracked) - the reference point a trailing stop is
    computed from. None (no prior high-water mark, e.g. right after entry)
    starts tracking from the current price."""
    if high_water_mark is None:
        return current_price
    return max(high_water_mark, current_price)


def compute_trailing_stop_price(high_water_mark: float, trailing_pct: float) -> float:
    """The candidate new stop price: trailing_pct below the best price
    seen so far, not below the current price - the whole point is that it
    only ever ratchets up as high_water_mark advances."""
    return high_water_mark * (1 - trailing_pct)


def should_raise_stop(
    current_stop_price: Optional[float],
    candidate_stop_price: float,
    min_improvement_pct: float = DEFAULT_MIN_IMPROVEMENT_PCT,
) -> bool:
    """Whether the resting stop should actually be replaced. `None` for
    current_stop_price (no stop is resting yet) always says yes - there's
    nothing to compare against and no protection to lose by placing one.
    Otherwise only moves for a MEANINGFUL improvement (see module
    docstring) - and never for a lower candidate, which would defeat the
    entire purpose of a trailing stop."""
    if current_stop_price is None or current_stop_price <= 0:
        return True
    if candidate_stop_price <= current_stop_price:
        return False
    improvement_pct = (candidate_stop_price - current_stop_price) / current_stop_price
    return improvement_pct >= min_improvement_pct
