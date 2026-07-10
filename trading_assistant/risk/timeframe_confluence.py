"""Higher-timeframe trend confluence.

The core "top-down analysis" discipline: never trust a single timeframe's
signal in isolation. A 1h BUY against a confirmed 4h downtrend is exactly
the retracement trap described throughout The Candlestick Trading Bible -
a trader who buys a pullback on a lower timeframe without checking that the
higher timeframe hasn't actually reversed gets run over when the dominant
trend resumes. Professional price action trading checks the higher
timeframe FIRST, then looks for an entry-timeframe signal that agrees.

Resamples the SAME 1h OHLCV history already flowing through the system into
a higher timeframe (4h by default) rather than fetching a separate feed -
zero extra API calls, and guaranteed internal consistency: the "4h" trend
is always computed from the exact same underlying data as the "1h" signal,
never a slightly-out-of-sync separate fetch. Regime classification reuses
agents.features.build_features/classify_regime directly - the same single
source of truth already used for live regime detection and tournament
fold-tagging, so a symbol's "4h regime" here means the identical thing a
"1h regime" means elsewhere in this system.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from trading_assistant.agents.features import build_features, classify_regime

# Below this many resampled higher-timeframe bars, ADX/SMA-slow warm-up
# can't produce a meaningful trend classification - "not enough data" must
# not silently masquerade as "ranging".
MIN_RESAMPLED_BARS = 35


def resample_ohlcv(frame: pd.DataFrame, factor: int = 4) -> pd.DataFrame:
    """Aggregate every `factor` consecutive rows into one higher-timeframe
    bar (open=first, high=max, low=min, close=last, volume=sum). A
    positional resample, not a calendar-aligned one: the caller's rolling
    window isn't guaranteed to start on a real 4h boundary, and a
    positional group-by is deterministic and leak-free regardless (each
    output bar is built only from bars that were already adjacent and
    already in the past - no lookahead)."""
    if factor < 1 or len(frame) < factor:
        return frame.iloc[0:0]

    # Trim from the FRONT so the last (most recent, most decision-relevant)
    # group is always complete; any leftover partial group is the oldest,
    # least relevant data and is simply dropped.
    usable_len = (len(frame) // factor) * factor
    trimmed = frame.iloc[len(frame) - usable_len :].reset_index(drop=True)
    group_id = trimmed.index // factor

    resampled = trimmed.groupby(group_id).agg(
        timestamp=("timestamp", "last"),
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    )
    return resampled.reset_index(drop=True)


def higher_timeframe_regime(frame: pd.DataFrame, factor: int = 4) -> Optional[str]:
    """The higher-timeframe regime ("trending_up" | "trending_down" |
    "ranging") for the most recent resampled bar, or None if there isn't
    enough resampled history to classify it meaningfully yet - "unknown"
    is a distinct, honest state from "ranging", not conflated with it."""
    resampled = resample_ohlcv(frame, factor)
    if len(resampled) < MIN_RESAMPLED_BARS:
        return None

    featured = build_features(resampled)
    last = featured.iloc[-1]
    trend_strength = last.get("sma_fast_vs_slow")
    adx_strength = last.get("adx_strength")
    if pd.isna(trend_strength) or pd.isna(adx_strength):
        return None
    return classify_regime(float(trend_strength), float(adx_strength))


def higher_timeframe_blocks_buy(higher_tf_regime: Optional[str]) -> bool:
    """True only when the higher timeframe is a CONFIRMED downtrend - a
    ranging or unclassifiable (insufficient history) higher timeframe does
    NOT block anything. "No clear opinion yet" is not the same claim as
    "the higher timeframe disagrees", and treating them the same would
    make this filter reject trades based on a data gap rather than an
    actual conflicting signal."""
    return higher_tf_regime == "trending_down"
