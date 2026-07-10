"""Volatility-adjusted stop distance (and, through it, position sizing).

A fixed stop_loss_pct applied identically to every symbol either sits too
close (getting stopped out on ordinary noise for a genuinely volatile
altcoin) or too far (barely protecting anything for a near-stablecoin pair
like PAXG/EUR/USDC/FDUSD - all of which sit in this system's tracked
universe alongside far more volatile alts, via symbol discovery). ATR
(average true range, already computed as atr_pct in agents.features - no
new indicator needed) is the standard measure of a symbol's own recent
volatility: scaling the stop distance to a multiple of it means every
symbol gets a stop calibrated to ITS OWN typical bar-to-bar movement, not
one borrowed from whatever symbol the fixed default happened to be tuned
against.

Because position size is itself computed from stop distance
(risk_amount / stop_loss_pct - see RiskManager.calculate_position_size),
this same volatility-adjusted stop distance also makes position sizing
volatility-aware for free: a wider stop (a more volatile symbol) sizes a
smaller position for the same dollar risk, and vice versa - exactly the
"reduce risk automatically in unstable conditions" behavior a fixed stop
distance can't provide.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

DEFAULT_ATR_MULTIPLIER = 2.0
DEFAULT_MIN_STOP_PCT = 0.005
DEFAULT_MAX_STOP_PCT = 0.05


def compute_atr_stop_pct(
    atr_pct: Optional[float],
    fallback_stop_pct: float,
    atr_multiplier: float = DEFAULT_ATR_MULTIPLIER,
    min_stop_pct: float = DEFAULT_MIN_STOP_PCT,
    max_stop_pct: float = DEFAULT_MAX_STOP_PCT,
) -> float:
    """A stop distance scaled to `atr_multiplier` times the symbol's own
    recent ATR (as a fraction of price), clamped to a sane [min, max]
    range so a barely-moving stablecoin pair doesn't get an effectively
    zero stop, and a violently volatile pair doesn't get one so wide it
    shrinks sizing to almost nothing. Falls back to `fallback_stop_pct`
    (the static default) when atr_pct isn't available (missing/NaN
    feature data, e.g. not enough warm-up history yet) - a data gap, not
    a real volatility reading, and must never block a trade."""
    if atr_pct is None or pd.isna(atr_pct) or atr_pct <= 0:
        return fallback_stop_pct
    candidate = atr_pct * atr_multiplier
    return max(min_stop_pct, min(candidate, max_stop_pct))
