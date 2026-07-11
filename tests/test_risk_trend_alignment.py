import numpy as np
import pandas as pd

from trading_assistant.risk.trend_alignment import (
    adx_blocks_buy,
    adx_confirms_trend,
    compute_sma_slope_rising,
    compute_trend_alignment,
    sma_slope_blocks_buy,
    trend_alignment_blocks_buy,
    volume_blocks_buy,
    volume_confirms_breakout,
)


def _close(values):
    return pd.Series(values, dtype=float)


def test_aligned_when_price_above_both_smas_in_order():
    # Flat history at 100 (both SMAs settle at 100), then price pushes to 110.
    values = [100.0] * 50 + [110.0]
    alignment = compute_trend_alignment(_close(values), fast_period=20, slow_period=50)
    assert alignment == "aligned"
    assert trend_alignment_blocks_buy(alignment) is False


def test_below_fast_only_is_the_milder_warning():
    # A steady uptrend (so SMA20, weighted toward the higher recent bars,
    # sits above SMA50 - the normal aligned ordering), then a sudden
    # one-bar drop that dips price below SMA20 but not below the lower
    # SMA50 - the milder of the two "not aligned" cases.
    uptrend = list(np.linspace(80, 100, 49))
    values = uptrend + [90.0]
    alignment = compute_trend_alignment(_close(values), fast_period=20, slow_period=50)
    assert alignment == "below_fast"
    assert trend_alignment_blocks_buy(alignment) is True


def test_below_slow_is_the_more_bearish_case():
    # A sustained decline long enough to drag both SMAs down with price
    # below both - the more cautious case.
    values = [100.0] * 50 + [float(100 - i) for i in range(1, 21)]
    alignment = compute_trend_alignment(_close(values), fast_period=20, slow_period=50)
    assert alignment == "below_slow"
    assert trend_alignment_blocks_buy(alignment) is True


def test_none_with_insufficient_history_does_not_block():
    alignment = compute_trend_alignment(_close([100.0] * 10), fast_period=20, slow_period=50)
    assert alignment is None
    assert trend_alignment_blocks_buy(alignment) is False


def test_none_when_close_series_is_none():
    assert compute_trend_alignment(None) is None


# ---- SMA slope ----

def test_slope_rising_when_sma_has_climbed_over_the_lookback():
    closes = _close(np.linspace(80.0, 100.0, 60))
    rising = compute_sma_slope_rising(closes, period=20, lookback=3)
    assert rising is True
    assert sma_slope_blocks_buy(rising) is False


def test_slope_not_rising_on_a_flat_average():
    closes = _close([100.0] * 60)
    rising = compute_sma_slope_rising(closes, period=20, lookback=3)
    assert rising is False
    assert sma_slope_blocks_buy(rising) is True


def test_slope_none_with_insufficient_history_does_not_block():
    rising = compute_sma_slope_rising(_close([100.0] * 10), period=20, lookback=3)
    assert rising is None
    assert sma_slope_blocks_buy(rising) is False


# ---- ADX confirmation ----

def test_adx_confirms_when_above_threshold():
    confirms = adx_confirms_trend(30.0, threshold=25.0)
    assert confirms is True
    assert adx_blocks_buy(confirms) is False


def test_adx_does_not_confirm_below_threshold():
    confirms = adx_confirms_trend(15.0, threshold=25.0)
    assert confirms is False
    assert adx_blocks_buy(confirms) is True


def test_adx_none_does_not_block():
    assert adx_confirms_trend(None) is None
    assert adx_blocks_buy(None) is False


# ---- Volume confirmation ----

def test_volume_confirms_at_or_above_average():
    confirms = volume_confirms_breakout(1.5, threshold=1.0)
    assert confirms is True
    assert volume_blocks_buy(confirms) is False


def test_volume_does_not_confirm_below_average():
    confirms = volume_confirms_breakout(0.5, threshold=1.0)
    assert confirms is False
    assert volume_blocks_buy(confirms) is True


def test_volume_none_does_not_block():
    assert volume_confirms_breakout(None) is None
    assert volume_blocks_buy(None) is False
