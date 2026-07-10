"""SMA trend-alignment confluence for new long entries.

Classic trend-following discipline, distinct from the SMA10/30 crossover
already used elsewhere (SMACrossoverStrategy, agents.features's
sma_fast_vs_slow) and from higher_timeframe_confluence's higher-timeframe
regime check: this is a same-timeframe "is the trend structure actually
intact" gate. A new long is only taken while price sits above a faster SMA
which is itself above a slower SMA (price > SMA20 > SMA50, the "golden
alignment") - buying into a market that's already lost this structure is
buying against a broken trend, regardless of what any single indicator or
model signal says in isolation.

Two distinct severities, not one flat "blocked" bit: price falling below
the fast SMA alone is the first, milder warning (avoid new longs); price
falling below BOTH SMAs is materially more bearish (the fast SMA has
already lost the slow SMA's support too) and warrants the stronger,
explicit "be more cautious" language in the resulting risk-reject reason,
not just a generic rejection indistinguishable from the milder case.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

DEFAULT_FAST_PERIOD = 20
DEFAULT_SLOW_PERIOD = 50


def compute_trend_alignment(
    close: Optional[pd.Series], fast_period: int = DEFAULT_FAST_PERIOD, slow_period: int = DEFAULT_SLOW_PERIOD,
) -> Optional[str]:
    """One of:
      "aligned"     - price > SMA_fast > SMA_slow - the only state a new
                      long should be taken in.
      "below_fast"  - price has fallen below the fast SMA (but not the
                      slow one) - avoid new longs, existing ones warrant a
                      closer watch.
      "below_slow"  - price is below BOTH SMAs - materially more bearish
                      than below_fast alone, warrants extra caution.
      None          - not enough history yet to compute both SMAs; a data
                      gap, not a bearish signal, and must not be treated
                      as one (see trend_alignment_blocks_buy).
    """
    if close is None or len(close) < slow_period:
        return None
    sma_fast = close.rolling(fast_period).mean().iloc[-1]
    sma_slow = close.rolling(slow_period).mean().iloc[-1]
    price = close.iloc[-1]
    if pd.isna(sma_fast) or pd.isna(sma_slow) or pd.isna(price):
        return None

    if price > sma_fast > sma_slow:
        return "aligned"
    if price < sma_slow:
        return "below_slow"
    return "below_fast"


def trend_alignment_blocks_buy(alignment: Optional[str]) -> bool:
    """Only the fully "aligned" state clears a new long - None (insufficient
    history) does NOT block, matching higher_timeframe_confluence's "fail
    open on missing data" convention: a data gap is not grounds to reject a
    trade the model/strategy otherwise approved."""
    return alignment is not None and alignment != "aligned"
