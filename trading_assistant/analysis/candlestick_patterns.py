"""Japanese candlestick reversal/continuation pattern detection.

Codifies the classic patterns (Homma/Nison-school price action: engulfing,
hammer/shooting star, harami, doji variants, morning/evening star) as pure,
vectorized functions over OHLC arrays - the same "single source of truth,
no train/serve skew" approach as agents/features.py.

Every detector returns a bool Series aligned to the input index: True on
the bar where the pattern COMPLETES (e.g. the second candle of an engulfing
pair, the third candle of a morning/evening star). Deliberately excludes
anything that requires subjective, hand-drawn context this codebase can't
yet compute mechanically (support/resistance zones, trendlines, supply/demand
areas, Fibonacci confluence) - those live in CandlestickPatternStrategy's
trend filter instead, using data already available (a rolling SMA), not
invented indicators.

One deliberate adaptation from the source material: morning/evening star's
classic definition includes an opening "gap" between candles two and three.
Crypto trades continuously with no session gaps (unlike the stocks/forex
these patterns were originally described for), so the gap requirement is
dropped - the substantive criteria (weak middle candle, third candle closing
past the first candle's body midpoint) are what's kept and are what actually
drives the pattern's meaning.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _body_top(open_: pd.Series, close: pd.Series) -> pd.Series:
    return pd.concat([open_, close], axis=1).max(axis=1)


def _body_bottom(open_: pd.Series, close: pd.Series) -> pd.Series:
    return pd.concat([open_, close], axis=1).min(axis=1)


def _body_size(open_: pd.Series, close: pd.Series) -> pd.Series:
    return (close - open_).abs()


def _candle_range(high: pd.Series, low: pd.Series) -> pd.Series:
    return (high - low).replace(0, np.nan)


def bullish_engulfing(open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """Prior candle bearish, current candle bullish and its body fully
    covers the prior body - "the second body engulfs the previous one"."""
    prev_open, prev_close = open_.shift(1), close.shift(1)
    prev_bearish = prev_close < prev_open
    curr_bullish = close > open_
    fully_covers = (open_ <= prev_close) & (close >= prev_open)
    bigger_body = _body_size(open_, close) > _body_size(prev_open, prev_close)
    return (prev_bearish & curr_bullish & fully_covers & bigger_body).fillna(False)


def bearish_engulfing(open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_open, prev_close = open_.shift(1), close.shift(1)
    prev_bullish = prev_close > prev_open
    curr_bearish = close < open_
    fully_covers = (open_ >= prev_close) & (close <= prev_open)
    bigger_body = _body_size(open_, close) > _body_size(prev_open, prev_close)
    return (prev_bullish & curr_bearish & fully_covers & bigger_body).fillna(False)


def hammer(open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series, wick_to_body: float = 2.0) -> pd.Series:
    """Small body, little/no upper shadow, a lower shadow at least
    `wick_to_body` times the body ("professional technicians say the shadow
    should be twice the length of the real body")."""
    body = _body_size(open_, close)
    lower_wick = _body_bottom(open_, close) - low
    upper_wick = high - _body_top(open_, close)
    rng = _candle_range(high, low)
    has_body = body > 0
    long_lower = lower_wick >= body * wick_to_body
    small_upper = upper_wick <= body * 0.5
    return (has_body & long_lower & small_upper & rng.notna()).fillna(False)


def shooting_star(
    open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series, wick_to_body: float = 2.0
) -> pd.Series:
    """Bearish mirror of hammer: small body, long upper shadow, little/no
    lower shadow."""
    body = _body_size(open_, close)
    lower_wick = _body_bottom(open_, close) - low
    upper_wick = high - _body_top(open_, close)
    rng = _candle_range(high, low)
    has_body = body > 0
    long_upper = upper_wick >= body * wick_to_body
    small_lower = lower_wick <= body * 0.5
    return (has_body & long_upper & small_lower & rng.notna()).fillna(False)


def doji(open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series, body_to_range_max: float = 0.1) -> pd.Series:
    """Open and close "the same or about the same price" - body is a
    negligible fraction of the full candle range."""
    body = _body_size(open_, close)
    rng = _candle_range(high, low)
    return ((body / rng) <= body_to_range_max).fillna(False)


def dragonfly_doji(open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """Doji with a long lower tail and little/no upper tail - bullish."""
    is_doji = doji(open_, high, low, close)
    upper_wick = high - _body_top(open_, close)
    lower_wick = _body_bottom(open_, close) - low
    rng = _candle_range(high, low)
    long_lower_tail = lower_wick >= rng * 0.6
    small_upper_tail = upper_wick <= rng * 0.1
    return (is_doji & long_lower_tail & small_upper_tail).fillna(False)


def gravestone_doji(open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """Doji with a long upper tail and little/no lower tail - bearish."""
    is_doji = doji(open_, high, low, close)
    upper_wick = high - _body_top(open_, close)
    lower_wick = _body_bottom(open_, close) - low
    rng = _candle_range(high, low)
    long_upper_tail = upper_wick >= rng * 0.6
    small_lower_tail = lower_wick <= rng * 0.1
    return (is_doji & long_upper_tail & small_lower_tail).fillna(False)


def bullish_harami(open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """Large bearish "mother" candle followed by a small bullish "baby"
    candle fully contained within the mother's body."""
    prev_open, prev_close = open_.shift(1), close.shift(1)
    prev_bearish = prev_close < prev_open
    curr_bullish = close > open_
    inside = (_body_top(open_, close) <= prev_open) & (_body_bottom(open_, close) >= prev_close)
    smaller_body = _body_size(open_, close) < _body_size(prev_open, prev_close)
    return (prev_bearish & curr_bullish & inside & smaller_body).fillna(False)


def bearish_harami(open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_open, prev_close = open_.shift(1), close.shift(1)
    prev_bullish = prev_close > prev_open
    curr_bearish = close < open_
    inside = (_body_top(open_, close) <= prev_close) & (_body_bottom(open_, close) >= prev_open)
    smaller_body = _body_size(open_, close) < _body_size(prev_open, prev_close)
    return (prev_bullish & curr_bearish & inside & smaller_body).fillna(False)


def morning_star(open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """Three candles: bearish, then a small-bodied one, then a bullish
    candle closing back above the first candle's body midpoint."""
    o1, c1 = open_.shift(2), close.shift(2)
    o2, c2 = open_.shift(1), close.shift(1)
    first_bearish = c1 < o1
    second_small = _body_size(o2, c2) < _body_size(o1, c1) * 0.5
    third_bullish = close > open_
    midpoint = (o1 + c1) / 2
    closes_past_midpoint = close > midpoint
    return (first_bearish & second_small & third_bullish & closes_past_midpoint).fillna(False)


def evening_star(open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    o1, c1 = open_.shift(2), close.shift(2)
    o2, c2 = open_.shift(1), close.shift(1)
    first_bullish = c1 > o1
    second_small = _body_size(o2, c2) < _body_size(o1, c1) * 0.5
    third_bearish = close < open_
    midpoint = (o1 + c1) / 2
    closes_past_midpoint = close < midpoint
    return (first_bullish & second_small & third_bearish & closes_past_midpoint).fillna(False)


def detect_all(frame: pd.DataFrame) -> pd.DataFrame:
    """Every detector above, evaluated on frame's open/high/low/close,
    returned as one bool-column-per-pattern DataFrame aligned to frame's index."""
    o, h, l, c = frame["open"], frame["high"], frame["low"], frame["close"]
    return pd.DataFrame(
        {
            "bullish_engulfing": bullish_engulfing(o, h, l, c),
            "bearish_engulfing": bearish_engulfing(o, h, l, c),
            "hammer": hammer(o, h, l, c),
            "shooting_star": shooting_star(o, h, l, c),
            "doji": doji(o, h, l, c),
            "dragonfly_doji": dragonfly_doji(o, h, l, c),
            "gravestone_doji": gravestone_doji(o, h, l, c),
            "bullish_harami": bullish_harami(o, h, l, c),
            "bearish_harami": bearish_harami(o, h, l, c),
            "morning_star": morning_star(o, h, l, c),
            "evening_star": evening_star(o, h, l, c),
        },
        index=frame.index,
    )
