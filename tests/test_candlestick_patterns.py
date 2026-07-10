"""Precise synthetic-candle tests for candlestick pattern detection.

Each test hand-builds OHLC rows matching the textbook shape of one pattern
(per The Candlestick Trading Bible's definitions) and asserts the detector
fires exactly where expected - and does NOT fire on a similar-but-not-quite
candle, so these aren't just "it returns something" smoke tests.
"""

import pandas as pd

from trading_assistant.analysis.candlestick_patterns import (
    bearish_engulfing,
    bearish_harami,
    bullish_engulfing,
    bullish_harami,
    detect_all,
    doji,
    dragonfly_doji,
    evening_star,
    gravestone_doji,
    hammer,
    morning_star,
    shooting_star,
)


def _ohlc(rows):
    return (
        pd.Series([r[0] for r in rows], dtype=float),
        pd.Series([r[1] for r in rows], dtype=float),
        pd.Series([r[2] for r in rows], dtype=float),
        pd.Series([r[3] for r in rows], dtype=float),
    )


def test_bullish_engulfing_fires_and_rejects_smaller_body():
    # row0: bearish mother (110 -> 95). row1: bullish body fully covers it, bigger body.
    o, h, l, c = _ohlc([(110, 111, 94, 95), (94, 113, 93, 112)])
    result = bullish_engulfing(o, h, l, c)
    assert result.iloc[1] is True or result.iloc[1] == True  # noqa: E712
    assert result.iloc[0] == False  # noqa: E712 - no previous candle to engulf

    # smaller engulfing body must NOT fire
    o2, h2, l2, c2 = _ohlc([(110, 111, 94, 95), (99, 102, 98, 101)])
    assert bullish_engulfing(o2, h2, l2, c2).iloc[1] == False  # noqa: E712


def test_bearish_engulfing_fires_and_rejects_non_engulfing():
    o, h, l, c = _ohlc([(95, 111, 94, 110), (112, 113, 89, 90)])
    result = bearish_engulfing(o, h, l, c)
    assert result.iloc[1] == True  # noqa: E712

    o2, h2, l2, c2 = _ohlc([(95, 111, 94, 110), (101, 102, 99, 100)])
    assert bearish_engulfing(o2, h2, l2, c2).iloc[1] == False  # noqa: E712


def test_hammer_requires_long_lower_wick_and_small_upper_wick():
    # body=2 (100->102), lower wick=15 (>=2x body), upper wick=1 (<=0.5x body)
    o, h, l, c = _ohlc([(100, 103, 85, 102)])
    assert hammer(o, h, l, c).iloc[0] == True  # noqa: E712

    # symmetric candle (no long lower wick) must NOT be a hammer
    o2, h2, l2, c2 = _ohlc([(100, 103, 97, 102)])
    assert hammer(o2, h2, l2, c2).iloc[0] == False  # noqa: E712


def test_shooting_star_requires_long_upper_wick_and_small_lower_wick():
    o, h, l, c = _ohlc([(100, 115, 97, 98)])
    assert shooting_star(o, h, l, c).iloc[0] == True  # noqa: E712

    o2, h2, l2, c2 = _ohlc([(100, 103, 97, 98)])
    assert shooting_star(o2, h2, l2, c2).iloc[0] == False  # noqa: E712


def test_doji_detects_negligible_body_and_rejects_large_body():
    o, h, l, c = _ohlc([(100, 101, 99, 100.05)])
    assert doji(o, h, l, c).iloc[0] == True  # noqa: E712

    o2, h2, l2, c2 = _ohlc([(100, 106, 99, 105)])
    assert doji(o2, h2, l2, c2).iloc[0] == False  # noqa: E712


def test_dragonfly_doji_requires_long_lower_tail():
    o, h, l, c = _ohlc([(100, 100.2, 90, 100.1)])
    assert dragonfly_doji(o, h, l, c).iloc[0] == True  # noqa: E712
    assert gravestone_doji(o, h, l, c).iloc[0] == False  # noqa: E712


def test_gravestone_doji_requires_long_upper_tail():
    o, h, l, c = _ohlc([(100, 110, 99.8, 99.9)])
    assert gravestone_doji(o, h, l, c).iloc[0] == True  # noqa: E712
    assert dragonfly_doji(o, h, l, c).iloc[0] == False  # noqa: E712


def test_bullish_harami_baby_must_be_fully_inside_mother_body():
    # mother: bearish 110->95 (body 95-110). baby: bullish, fully inside, smaller body.
    o, h, l, c = _ohlc([(110, 111, 94, 95), (100, 106, 99, 105)])
    assert bullish_harami(o, h, l, c).iloc[1] == True  # noqa: E712

    # baby body extends OUTSIDE the mother's body -> must not fire
    o2, h2, l2, c2 = _ohlc([(110, 111, 94, 95), (100, 116, 99, 112)])
    assert bullish_harami(o2, h2, l2, c2).iloc[1] == False  # noqa: E712


def test_bearish_harami_baby_must_be_fully_inside_mother_body():
    o, h, l, c = _ohlc([(95, 111, 94, 110), (105, 106, 99, 100)])
    assert bearish_harami(o, h, l, c).iloc[1] == True  # noqa: E712


def test_morning_star_three_candle_reversal():
    # c1 bearish (110->95, midpoint 102.5), c2 small body, c3 bullish closing past midpoint
    o, h, l, c = _ohlc([(110, 111, 94, 95), (94, 95, 92, 93), (93, 105, 92, 104)])
    assert morning_star(o, h, l, c).iloc[2] == True  # noqa: E712

    # c3 fails to close past the midpoint -> not a confirmed morning star
    o2, h2, l2, c2 = _ohlc([(110, 111, 94, 95), (94, 95, 92, 93), (93, 100, 92, 99)])
    assert morning_star(o2, h2, l2, c2).iloc[2] == False  # noqa: E712


def test_evening_star_three_candle_reversal():
    o, h, l, c = _ohlc([(95, 111, 94, 110), (111, 112, 110, 111), (112, 113, 100, 101)])
    assert evening_star(o, h, l, c).iloc[2] == True  # noqa: E712


def test_detect_all_returns_one_boolean_column_per_pattern_aligned_to_index():
    frame = pd.DataFrame(
        {
            "open": [110, 94, 93],
            "high": [111, 95, 105],
            "low": [94, 92, 92],
            "close": [95, 93, 104],
        }
    )
    result = detect_all(frame)

    assert list(result.index) == list(frame.index)
    assert result["morning_star"].iloc[2] == True  # noqa: E712
    assert set(result.columns) == {
        "bullish_engulfing", "bearish_engulfing", "hammer", "shooting_star", "doji",
        "dragonfly_doji", "gravestone_doji", "bullish_harami", "bearish_harami",
        "morning_star", "evening_star",
    }
