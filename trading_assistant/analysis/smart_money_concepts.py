"""Smart Money Concepts (ICT-style) market-structure detection: swing
highs/lows, structure state (bullish/bearish/ranging), break of structure
(BOS), market structure shift (MSS), and order block zones.

Same "single source of truth, pure functions over OHLC" approach as
analysis/candlestick_patterns.py and agents/features.py.

Causality is the one thing to get exactly right here, more than for a
simple rolling indicator: a swing high/low at bar i is only a REAL swing
point once `lookback` bars have passed on both sides - you cannot know a
bar was a local high until you've seen what came after it. swing_highs/
swing_lows below are the raw, retrospective labels (True exactly at the
bar that turned out to be the swing point - useful for charting/analysis,
same convention as candlestick_patterns' "True on the bar the pattern
completes"). compute_market_structure builds structure_state/bos/mss from
only the swing points CONFIRMED as of each row (a swing at index i is
knowable starting at row i + lookback, not before) - anything used as a
live feature or fed to the ML model must go through this causal path,
never the raw swing_highs/swing_lows directly, or it silently leaks
future information into what's supposed to be a live-safe signal.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

DEFAULT_SWING_LOOKBACK = 2
DEFAULT_DISPLACEMENT_ATR_MULTIPLE = 2.0


def swing_highs(high: pd.Series, lookback: int = DEFAULT_SWING_LOOKBACK) -> pd.Series:
    """True where `high` is a strict local maximum over the `lookback`
    bars on EITHER side - a retrospective label. NaN-propagated edges
    (the first/last `lookback` bars can never be confirmed, since one
    side's window is incomplete) come back False, not fabricated."""
    is_max = pd.Series(True, index=high.index)
    for k in range(1, lookback + 1):
        is_max &= (high > high.shift(k)) & (high > high.shift(-k))
    return is_max.fillna(False)


def swing_lows(low: pd.Series, lookback: int = DEFAULT_SWING_LOOKBACK) -> pd.Series:
    """True where `low` is a strict local minimum - mirror of swing_highs."""
    is_min = pd.Series(True, index=low.index)
    for k in range(1, lookback + 1):
        is_min &= (low < low.shift(k)) & (low < low.shift(-k))
    return is_min.fillna(False)


def compute_market_structure(
    high: pd.Series, low: pd.Series, close: pd.Series, lookback: int = DEFAULT_SWING_LOOKBACK
) -> pd.DataFrame:
    """Causal market structure: at each row, uses only swing points
    confirmed as of that row (see module docstring). Returns a DataFrame
    aligned to the input index with:

      structure_state: "bullish" (last two confirmed swing highs AND the
        last two confirmed swing lows are both ascending), "bearish"
        (both descending), or "ranging" (mixed, or fewer than two
        confirmed swings of either kind yet - a data gap, not a real
        ranging call).
      bos: +1 where close breaks above the most recent confirmed swing
        high while structure was bullish (continuation), -1 for the
        bearish mirror, 0 otherwise.
      mss: +1 where close breaks above the most recent confirmed swing
        high while structure was BEARISH (reversal), -1 for the bullish
        mirror, 0 otherwise. Mutually exclusive with bos at any given row
        by construction (a break either continues or reverses the prior
        structure, never both).
    """
    n = len(high)
    idx = high.index
    h = high.to_numpy(dtype=float)
    l = low.to_numpy(dtype=float)
    c = close.to_numpy(dtype=float)

    raw_swing_high = swing_highs(high, lookback).to_numpy()
    raw_swing_low = swing_lows(low, lookback).to_numpy()

    structure_state = np.array(["ranging"] * n, dtype=object)
    bos = np.zeros(n, dtype=int)
    mss = np.zeros(n, dtype=int)

    confirmed_highs: list = []
    confirmed_lows: list = []
    current_structure = "ranging"

    for j in range(n):
        confirm_idx = j - lookback
        if confirm_idx >= 0:
            if raw_swing_high[confirm_idx]:
                confirmed_highs.append(h[confirm_idx])
            if raw_swing_low[confirm_idx]:
                confirmed_lows.append(l[confirm_idx])

        if confirmed_highs and current_structure == "bullish" and c[j] > confirmed_highs[-1]:
            bos[j] = 1
        elif confirmed_lows and current_structure == "bearish" and c[j] < confirmed_lows[-1]:
            bos[j] = -1
        elif confirmed_highs and current_structure == "bearish" and c[j] > confirmed_highs[-1]:
            mss[j] = 1
        elif confirmed_lows and current_structure == "bullish" and c[j] < confirmed_lows[-1]:
            mss[j] = -1

        if len(confirmed_highs) >= 2 and len(confirmed_lows) >= 2:
            highs_ascending = confirmed_highs[-1] > confirmed_highs[-2]
            lows_ascending = confirmed_lows[-1] > confirmed_lows[-2]
            if highs_ascending and lows_ascending:
                current_structure = "bullish"
            elif not highs_ascending and not lows_ascending:
                current_structure = "bearish"
            else:
                current_structure = "ranging"
        else:
            current_structure = "ranging"

        structure_state[j] = current_structure

    return pd.DataFrame({"structure_state": structure_state, "bos": bos, "mss": mss}, index=idx)


def _order_block_zones(
    open_: pd.Series,
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    atr: pd.Series,
    bullish: bool,
    displacement_atr_multiple: float,
) -> pd.DataFrame:
    n = len(close)
    idx = close.index
    o = open_.to_numpy(dtype=float)
    h = high.to_numpy(dtype=float)
    l = low.to_numpy(dtype=float)
    c = close.to_numpy(dtype=float)
    a = atr.to_numpy(dtype=float)

    zone_high = np.full(n, np.nan)
    zone_low = np.full(n, np.nan)
    is_ob = np.zeros(n, dtype=bool)

    active_high: Optional[float] = None
    active_low: Optional[float] = None

    for i in range(1, n):
        # Check mitigation of whatever zone was ALREADY active BEFORE this
        # bar first - not the zone this same bar is about to form below.
        # The displacement bar that confirms a new zone is, by definition,
        # moving away from it; testing that bar's own high/low against
        # the zone it just created immediately clears zones that were
        # never actually exposed as active in the output at all (a real
        # bug caught by test_zone_is_mitigated_once_price_trades_back_
        # into_it failing during development).
        if active_high is not None:
            mitigated = (l[i] <= active_high and l[i] >= active_low) if bullish else (
                h[i] >= active_low and h[i] <= active_high
            )
            # A pullback that trades all the way THROUGH the zone (not just
            # touching it) has consumed it just as much as one that closes
            # inside it - mitigated either way, not still "active" after.
            traded_through = (l[i] < active_low) if bullish else (h[i] > active_high)
            if mitigated or traded_through:
                active_high = None
                active_low = None

        prev_is_setup_candle = (c[i - 1] < o[i - 1]) if bullish else (c[i - 1] > o[i - 1])
        displacement = (c[i] - o[i]) if bullish else (o[i] - c[i])
        atr_ref = a[i - 1]
        if (
            prev_is_setup_candle
            and not np.isnan(atr_ref)
            and atr_ref > 0
            and displacement > displacement_atr_multiple * atr_ref
        ):
            active_high = h[i - 1]
            active_low = l[i - 1]
            is_ob[i - 1] = True

        zone_high[i] = active_high if active_high is not None else np.nan
        zone_low[i] = active_low if active_low is not None else np.nan

    return pd.DataFrame(
        {f"{'bullish' if bullish else 'bearish'}_ob": is_ob, "zone_high": zone_high, "zone_low": zone_low}, index=idx
    )


def bullish_order_block_zones(
    open_: pd.Series,
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    atr: pd.Series,
    displacement_atr_multiple: float = DEFAULT_DISPLACEMENT_ATR_MULTIPLE,
) -> pd.DataFrame:
    """The last bearish candle immediately before a strong bullish
    displacement move (next candle's body exceeds `displacement_atr_multiple`
    times the prior bar's ATR) - the zone (that candle's high/low) is
    "active" (unmitigated) until price trades back into or through it,
    at which point it's considered consumed. Returns columns bullish_ob
    (bool, True on the setup candle itself), zone_high, zone_low (the
    currently-active zone's bounds, NaN when no zone is active) - both
    forward-carried automatically by the single pass below, not a
    separate ffill step, so a mitigation is reflected on the exact bar
    it happens rather than one bar late."""
    return _order_block_zones(open_, high, low, close, atr, bullish=True, displacement_atr_multiple=displacement_atr_multiple)


def bearish_order_block_zones(
    open_: pd.Series,
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    atr: pd.Series,
    displacement_atr_multiple: float = DEFAULT_DISPLACEMENT_ATR_MULTIPLE,
) -> pd.DataFrame:
    """Mirror of bullish_order_block_zones for bearish displacement moves."""
    return _order_block_zones(open_, high, low, close, atr, bullish=False, displacement_atr_multiple=displacement_atr_multiple)
