"""Entry confluence for new long entries: SMA trend-alignment, SMA slope,
ADX trend-strength, and volume confirmation - four independent checks
that together answer "is this actually a good moment to buy," not just
"did the model/strategy say BUY."

Classic trend-following discipline, distinct from the SMA10/30 crossover
already used elsewhere (SMACrossoverStrategy, agents.features's
sma_fast_vs_slow) and from higher_timeframe_confluence's higher-timeframe
regime check: this is a same-timeframe "is the trend structure actually
intact" gate. A new long is only taken while:
  1. price > SMA_fast > SMA_slow (the "golden alignment", price=SMA20/50
     by default) - buying into a market that's already lost this
     structure is buying against a broken trend.
  2. the fast SMA is itself sloping upward - alignment alone can be true
     right after a sharp spike that hasn't yet turned the average's own
     direction, a materially weaker claim than a sustained climb.
  3. ADX confirms the trend has real strength (not just drifting in a
     ranging market that happens to satisfy 1 and 2 by chance).
  4. volume confirms real participation behind the move (a "breakout" on
     thin volume is far more likely to fail).

Each check is independent and independently testable; RiskAgent.evaluate_buy
applies all four (each opt-in on its own) and reports whichever one first
rejects a candidate, so the rejection reason always says WHY, not just
"no." Two distinct severities for the core alignment check, not one flat
"blocked" bit: price falling below the fast SMA alone is the first, milder
warning (avoid new longs); price falling below BOTH SMAs is materially
more bearish and warrants the stronger, explicit "be more cautious"
language in the resulting risk-reject reason.
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


DEFAULT_SLOPE_LOOKBACK = 3


def compute_sma_slope_rising(
    close: Optional[pd.Series], period: int = DEFAULT_FAST_PERIOD, lookback: int = DEFAULT_SLOPE_LOOKBACK,
) -> Optional[bool]:
    """Whether the fast SMA is itself trending upward - price > SMA_fast >
    SMA_slow alone can be true right after a sharp spike that hasn't yet
    turned the average's own direction, which is a materially weaker claim
    than "the average has been climbing for several bars." Compares the
    SMA's current value against its own value `lookback` bars ago, not just
    bar-to-bar noise (a single-bar wiggle in a genuinely flat average
    shouldn't flip this). None if there isn't enough history to compute the
    SMA at both points - a data gap, not a bearish signal."""
    if close is None or len(close) < period + lookback:
        return None
    sma = close.rolling(period).mean()
    current, previous = sma.iloc[-1], sma.iloc[-1 - lookback]
    if pd.isna(current) or pd.isna(previous):
        return None
    return bool(current > previous)


def sma_slope_blocks_buy(is_rising: Optional[bool]) -> bool:
    """None (insufficient history) does NOT block - same "data gap isn't a
    veto" convention as every other confluence check here."""
    return is_rising is not None and not is_rising


DEFAULT_ADX_THRESHOLD = 25.0


def adx_confirms_trend(adx_value: Optional[float], threshold: float = DEFAULT_ADX_THRESHOLD) -> Optional[bool]:
    """ADX above `threshold` (0-100 scale, the standard technical-analysis
    convention - 25 is the common "market is trending, not just drifting"
    bar) confirms the trend has enough real strength behind it to act on;
    below it, a same-timeframe SMA alignment could just be noise in a
    ranging market. None (no ADX value available yet) is a data gap, not a
    confirmed-weak-trend signal."""
    if adx_value is None or pd.isna(adx_value):
        return None
    return bool(adx_value >= threshold)


def adx_blocks_buy(confirms: Optional[bool]) -> bool:
    return confirms is not None and not confirms


DEFAULT_VOLUME_THRESHOLD = 1.0


def volume_confirms_breakout(relative_volume: Optional[float], threshold: float = DEFAULT_VOLUME_THRESHOLD) -> Optional[bool]:
    """Relative volume (current bar's volume / its own rolling average - see
    agents.features.build_features's relative_volume) at or above
    `threshold` (1.0 = at least the recent average) confirms real
    participation behind the move, not a breakout on thin volume that's
    more likely to fail. None (no volume data yet) is a data gap."""
    if relative_volume is None or pd.isna(relative_volume):
        return None
    return bool(relative_volume >= threshold)


def volume_blocks_buy(confirms: Optional[bool]) -> bool:
    return confirms is not None and not confirms
