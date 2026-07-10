import pandas as pd

from trading_assistant.strategies.base import Signal
from trading_assistant.strategies.implementations import CandlestickPatternStrategy


def _flat_trend_candles(n, start, step):
    """n candles with negligible range (no accidental pattern matches),
    closing at a steady linear trend - just enough to drive the SMA filter."""
    closes = [start + i * step for i in range(n)]
    return [(c, c + 0.01, c - 0.01, c) for c in closes]


def _to_series(rows):
    return (
        pd.Series([r[0] for r in rows], dtype=float),
        pd.Series([r[1] for r in rows], dtype=float),
        pd.Series([r[2] for r in rows], dtype=float),
        pd.Series([r[3] for r in rows], dtype=float),
    )


def test_buys_a_hammer_after_a_confirmed_downtrend():
    strategy = CandlestickPatternStrategy(trend_period=20)
    # sma.iloc[-trend_period] itself needs a full trailing window to be
    # non-NaN, so the trend-direction comparison needs roughly 2x
    # trend_period of history before the pattern candle.
    downtrend = _flat_trend_candles(45, start=200, step=-1.5)  # steadily declining SMA
    hammer_candle = (100, 103, 85, 102)  # small bullish body, long lower wick, tiny upper wick
    o, h, l, c = _to_series(downtrend + [hammer_candle])

    signal = strategy.generate_signal({"open": o, "high": h, "low": l, "close": c})

    assert signal == Signal.BUY


def test_sells_a_shooting_star_after_a_confirmed_uptrend():
    strategy = CandlestickPatternStrategy(trend_period=20)
    uptrend = _flat_trend_candles(45, start=50, step=1.5)  # steadily rising SMA
    shooting_star_candle = (100, 115, 97, 98)  # small bearish body, long upper wick
    o, h, l, c = _to_series(uptrend + [shooting_star_candle])

    signal = strategy.generate_signal({"open": o, "high": h, "low": l, "close": c})

    assert signal == Signal.SELL


def test_holds_when_no_pattern_confirms_the_trend():
    strategy = CandlestickPatternStrategy(trend_period=20)
    downtrend = _flat_trend_candles(46, start=200, step=-1.5)  # no reversal candle appended
    o, h, l, c = _to_series(downtrend)

    signal = strategy.generate_signal({"open": o, "high": h, "low": l, "close": c})

    assert signal == Signal.HOLD


def test_ignores_a_hammer_that_forms_mid_uptrend():
    """The book's core point: a hammer is only meaningful as a reversal
    signal at the end of a down move - the same candle appearing mid-uptrend
    is not a buy signal, since there's no downtrend for it to reverse."""
    strategy = CandlestickPatternStrategy(trend_period=20)
    uptrend = _flat_trend_candles(45, start=50, step=1.5)
    hammer_candle = (100, 103, 85, 102)
    o, h, l, c = _to_series(uptrend + [hammer_candle])

    signal = strategy.generate_signal({"open": o, "high": h, "low": l, "close": c})

    assert signal == Signal.HOLD


def test_holds_with_insufficient_history():
    strategy = CandlestickPatternStrategy(trend_period=20)
    o, h, l, c = _to_series(_flat_trend_candles(10, start=100, step=1.0))

    assert strategy.generate_signal({"open": o, "high": h, "low": l, "close": c}) == Signal.HOLD


def test_holds_when_ohlc_data_missing():
    strategy = CandlestickPatternStrategy()
    assert strategy.generate_signal({"prices": pd.Series([100.0, 101.0])}) == Signal.HOLD
