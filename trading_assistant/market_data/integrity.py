"""Market-data integrity guard: nothing before this checked whether
freshly-fetched OHLCV was actually SANE before it flowed into regime
classification, feature engineering, and ultimately a live trading
decision. A fetch can nominally "succeed" (no exception, a non-empty
frame) while still returning garbage - an exchange-side glitch, a stale
cached response, a delisted/relisted symbol with a nonsensical opening
print, or corrupted OHLC relationships - and every one of those would
otherwise flow silently downstream.

This is a THIRD category, distinct from the two this system already
distinguishes everywhere else: not a "data gap" (missing/insufficient -
must never block a trade) and not a "confirmed adverse market condition"
(a real downtrend, a wide spread - SHOULD block). This is "the data
itself cannot be trusted", and untrustworthy data must never be silently
treated as either "no signal" or "a real signal" - it has to stop the
pipeline here, before either of those downstream decisions gets made.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

import pandas as pd

# Crypto is volatile, but a >50% move between two consecutive CLOSES on a
# symbol liquid enough to be in this system's tracked universe at all is
# far more consistent with a data glitch (a bad print, a feed splicing in
# a different symbol's bar) than a genuine single-bar move - a real flash
# crash of that size would itself be exactly the kind of extreme event
# worth pausing on rather than trading through blindly either way.
DEFAULT_MAX_SINGLE_BAR_MOVE_PCT = 0.5
# How many multiples of the timeframe's own bar length to allow before the
# latest bar counts as "stale" - bars close a little after their nominal
# boundary and poll timing has some jitter, so a bare 1x threshold would
# flag perfectly normal data as anomalous.
DEFAULT_MAX_STALENESS_MULTIPLE = 3.0


def detect_structural_anomalies(frame: pd.DataFrame) -> List[str]:
    """Row-level OHLC sanity checks across the whole fetched frame - things
    that are impossible for ANY real market bar, not just implausible."""
    issues: List[str] = []
    if frame.empty:
        return issues

    # NaN comparisons (<=, <, >, >=) always evaluate False in pandas/numpy,
    # so a malformed field from the exchange (feed.py's pd.to_numeric(...,
    # errors="coerce") turns anything unparseable into NaN) would silently
    # pass every comparison-based check below - exactly the "corrupted bar
    # slips through undetected" failure this module exists to prevent.
    # Must be checked explicitly, first, before any comparison-based check.
    if frame[["open", "high", "low", "close", "volume"]].isna().any().any():
        issues.append("NaN found in open/high/low/close/volume")

    if (frame[["open", "high", "low", "close"]] <= 0).any().any():
        issues.append("non-positive price found in open/high/low/close")

    if (frame["volume"] < 0).any():
        issues.append("negative volume found")

    if (frame["high"] < frame["low"]).any():
        issues.append("high < low on at least one bar")

    if (frame["high"] < frame[["open", "close"]].max(axis=1)).any():
        issues.append("high is below the bar's own open/close on at least one bar")

    if (frame["low"] > frame[["open", "close"]].min(axis=1)).any():
        issues.append("low is above the bar's own open/close on at least one bar")

    return issues


def detect_timestamp_anomalies(frame: pd.DataFrame) -> List[str]:
    """Ordering/uniqueness checks - a feed silently returning out-of-order
    or duplicated bars would corrupt every rolling/lookback feature
    computed on top of it, in a way structural OHLC checks alone can't see."""
    issues: List[str] = []
    if frame.empty or len(frame) < 2:
        return issues
    timestamps = pd.to_datetime(frame["timestamp"])
    if not timestamps.is_monotonic_increasing:
        issues.append("timestamps are not strictly increasing")
    if timestamps.duplicated().any():
        issues.append("duplicate timestamps found")
    return issues


def detect_extreme_move(
    frame: pd.DataFrame, max_single_bar_move_pct: Optional[float] = DEFAULT_MAX_SINGLE_BAR_MOVE_PCT
) -> Optional[str]:
    """Flags only the MOST RECENT bar's move relative to the prior close -
    a live decision is driven off the latest bar, so that's the one
    single-bar move that actually matters for "should this cycle trust the
    data it's about to decide on", not some historical anomaly buried
    deep in the warm-up window. `max_single_bar_move_pct=None` disables
    this specific check (opt-in at the caller level)."""
    if max_single_bar_move_pct is None or len(frame) < 2:
        return None
    prev_close = float(frame["close"].iloc[-2])
    last_close = float(frame["close"].iloc[-1])
    if prev_close <= 0:
        return None
    move_pct = abs(last_close - prev_close) / prev_close
    if move_pct > max_single_bar_move_pct:
        return f"latest bar moved {move_pct:.1%} from the prior close (threshold {max_single_bar_move_pct:.0%})"
    return None


def detect_staleness(
    frame: pd.DataFrame,
    timeframe_seconds: float,
    max_staleness_multiple: float = DEFAULT_MAX_STALENESS_MULTIPLE,
    now: Optional[datetime] = None,
) -> Optional[str]:
    """Flags a feed that's technically still returning bars but hasn't
    produced a NEW one in a while - e.g. an exchange serving a cached
    response, or a symbol quietly halted."""
    if frame.empty:
        return None
    now = now or datetime.now(timezone.utc)
    last_ts = pd.to_datetime(frame["timestamp"].iloc[-1])
    if last_ts.tzinfo is None:
        last_ts = last_ts.tz_localize(timezone.utc)
    staleness_seconds = (now - last_ts).total_seconds()
    max_allowed = timeframe_seconds * max_staleness_multiple
    if staleness_seconds > max_allowed:
        return f"latest bar is {staleness_seconds / 60:.0f} minute(s) old (allowed up to {max_allowed / 60:.0f})"
    return None


def detect_anomalies(
    frame: pd.DataFrame,
    timeframe_seconds: Optional[float] = None,
    max_single_bar_move_pct: Optional[float] = DEFAULT_MAX_SINGLE_BAR_MOVE_PCT,
    max_staleness_multiple: float = DEFAULT_MAX_STALENESS_MULTIPLE,
    now: Optional[datetime] = None,
) -> List[str]:
    """All checks combined - the single entry point callers use per symbol
    per cycle. `timeframe_seconds=None` skips the staleness check (opt-in,
    since it needs to know the expected bar cadence); `max_single_bar_move_pct=None`
    skips the extreme-move check. Structural OHLC and timestamp-ordering
    checks always run - there's no legitimate reading of real market data
    where high < low or a negative price is correct, so those need no
    opt-in."""
    issues = detect_structural_anomalies(frame)
    issues += detect_timestamp_anomalies(frame)

    extreme_move = detect_extreme_move(frame, max_single_bar_move_pct)
    if extreme_move:
        issues.append(extreme_move)

    if timeframe_seconds is not None:
        staleness = detect_staleness(frame, timeframe_seconds, max_staleness_multiple, now=now)
        if staleness:
            issues.append(staleness)

    return issues
