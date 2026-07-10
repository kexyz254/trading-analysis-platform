import numpy as np
import pandas as pd

from trading_assistant.risk.trend_alignment import compute_trend_alignment, trend_alignment_blocks_buy


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
