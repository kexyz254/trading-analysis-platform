import pandas as pd
import pytest

from trading_assistant.analysis.smart_money_concepts import (
    bearish_order_block_zones,
    bullish_order_block_zones,
    compute_market_structure,
    swing_highs,
    swing_lows,
)


def _series(values):
    return pd.Series(values, dtype=float)


class TestSwingHighsAndLows:
    def test_detects_an_obvious_local_maximum(self):
        # 1,2,3,10,3,2,1 - index 3 (value 10) is a clear swing high with
        # lookback=2 (strictly greater than 2 bars either side).
        high = _series([1, 2, 3, 10, 3, 2, 1])
        result = swing_highs(high, lookback=2)
        assert list(result) == [False, False, False, True, False, False, False]

    def test_detects_an_obvious_local_minimum(self):
        low = _series([10, 9, 8, 1, 8, 9, 10])
        result = swing_lows(low, lookback=2)
        assert list(result) == [False, False, False, True, False, False, False]

    def test_edges_within_lookback_distance_are_never_confirmed(self):
        # A peak at index 1 can't be confirmed with lookback=2 - there
        # aren't 2 bars available before it.
        high = _series([5, 10, 1, 1, 1])
        result = swing_highs(high, lookback=2)
        assert result.iloc[1] == False  # noqa: E712 - explicit bool compare reads clearer here
        assert result.iloc[-1] == False  # noqa: E712

    def test_flat_top_is_not_a_swing_high(self):
        # Two equal peak values - neither is STRICTLY greater than the
        # other, so neither qualifies (an ambiguous double-top, not a
        # single confirmed swing point).
        high = _series([1, 2, 10, 10, 2, 1])
        result = swing_highs(high, lookback=1)
        assert not result.iloc[2]
        assert not result.iloc[3]

    def test_no_swings_in_a_monotonic_series(self):
        high = _series([1, 2, 3, 4, 5, 6, 7])
        result = swing_highs(high, lookback=2)
        assert not result.any()


class TestMarketStructure:
    """Swing highs/lows are detected independently on the high and low
    series respectively - each test below constructs THEM separately
    (not from one shared close series) so that "baseline" filler bars
    between the notable swings are unambiguous non-extrema in BOTH
    series, and the two confirmed swing highs / two confirmed swing lows
    have distinct, deliberately ordered values traced by hand."""

    # low has confirmed swing lows at index 1 (value 5) and index 5
    # (value 7) - ascending (5 -> 7). high has confirmed swing highs at
    # index 3 (value 15) and index 7 (value 20) - ascending (15 -> 20).
    _BULLISH_LOW = [10, 5, 10, 10, 10, 7, 10, 10, 10]
    _BULLISH_HIGH = [10, 10, 10, 15, 10, 10, 10, 20, 10]

    # Mirror: swing lows descending (8 -> 5), swing highs descending (20 -> 15).
    _BEARISH_LOW = [10, 8, 10, 10, 10, 5, 10, 10, 10]
    _BEARISH_HIGH = [10, 10, 10, 20, 10, 10, 10, 15, 10]

    def test_ascending_swings_produce_bullish_structure(self):
        low, high = _series(self._BULLISH_LOW), _series(self._BULLISH_HIGH)
        close = low  # doesn't affect structure_state itself, only bos/mss
        result = compute_market_structure(high, low, close, lookback=1)
        assert result["structure_state"].iloc[-1] == "bullish"

    def test_descending_swings_produce_bearish_structure(self):
        low, high = _series(self._BEARISH_LOW), _series(self._BEARISH_HIGH)
        close = low
        result = compute_market_structure(high, low, close, lookback=1)
        assert result["structure_state"].iloc[-1] == "bearish"

    def test_too_few_confirmed_swings_is_ranging_not_fabricated(self):
        closes = [1, 2, 3, 4, 5]
        high = low = close = _series(closes)
        result = compute_market_structure(high, low, close, lookback=2)
        assert (result["structure_state"] == "ranging").all()

    def test_bos_fires_on_continuation_break_above_confirmed_swing_high(self):
        # Establish bullish structure, then a final bar whose close breaks
        # decisively above the last confirmed swing high (20).
        low = _series(self._BULLISH_LOW + [99])
        high = _series(self._BULLISH_HIGH + [101])
        close = _series(self._BULLISH_LOW[:-1] + [10, 100])
        result = compute_market_structure(high, low, close, lookback=1)
        assert result["structure_state"].iloc[-2] == "bullish"
        assert result["bos"].iloc[-1] == 1

    def test_mss_fires_on_reversal_break_against_bearish_structure(self):
        # Establish bearish structure, then break UP above the last
        # confirmed swing high - a reversal, not a continuation.
        low = _series(self._BEARISH_LOW + [99])
        high = _series(self._BEARISH_HIGH + [101])
        close = _series(self._BEARISH_LOW[:-1] + [10, 100])
        result = compute_market_structure(high, low, close, lookback=1)
        assert result["structure_state"].iloc[-2] == "bearish"
        assert result["mss"].iloc[-1] == 1
        assert result["bos"].iloc[-1] == 0  # mutually exclusive with bos

    def test_confirmation_is_causal_not_using_bars_from_the_future(self):
        """The core correctness property: a swing point at index i must
        not affect structure_state/bos/mss at any row EARLIER than
        i + lookback - using it sooner would leak future information
        into what must be a live-safe, causal feature."""
        low, high = _series(self._BULLISH_LOW), _series(self._BULLISH_HIGH)
        close = low
        full = compute_market_structure(high, low, close, lookback=1)
        # Truncating the series to hide everything from some row `cutoff`
        # onward must not change structure_state at any row BEFORE
        # cutoff - if it did, the fuller version was using future bars.
        cutoff = 7
        truncated = compute_market_structure(high[:cutoff], low[:cutoff], close[:cutoff], lookback=1)
        assert list(full["structure_state"].iloc[:cutoff]) == list(truncated["structure_state"])


class TestOrderBlockZones:
    def test_bullish_ob_forms_before_a_strong_displacement_move(self):
        # Bar 2 is bearish (close < open): open=10, close=8. Bar 3 displaces
        # up hard: open=8, close=20 (body=12), prior bar's ATR=1 ->
        # 12 > 2.0*1, comfortably clears the displacement threshold.
        open_ = _series([10, 10, 10, 8, 20])
        high = _series([11, 11, 11, 9, 21])
        low = _series([9, 9, 8, 7, 19])
        close = _series([10, 10, 8, 20, 20])
        atr = _series([1, 1, 1, 1, 1])

        result = bullish_order_block_zones(open_, high, low, close, atr, displacement_atr_multiple=2.0)

        assert result["bullish_ob"].iloc[2] == True  # noqa: E712
        assert result["zone_high"].iloc[3] == pytest.approx(high.iloc[2])
        assert result["zone_low"].iloc[3] == pytest.approx(low.iloc[2])

    def test_no_ob_when_displacement_is_too_small(self):
        open_ = _series([10, 10, 10, 8, 8.5])
        high = _series([11, 11, 11, 9, 9])
        low = _series([9, 9, 8, 7, 8])
        close = _series([10, 10, 8, 8.5, 8.5])
        atr = _series([1, 1, 1, 1, 1])

        result = bullish_order_block_zones(open_, high, low, close, atr, displacement_atr_multiple=2.0)

        assert not result["bullish_ob"].any()
        assert result["zone_high"].isna().all()

    def test_zone_is_mitigated_once_price_trades_back_into_it(self):
        # Same setup as the forming test, then a later bar's low dips
        # back into [zone_low, zone_high] - the zone must clear from
        # that bar onward.
        open_ = _series([10, 10, 10, 8, 20, 20, 20])
        high = _series([11, 11, 11, 9, 21, 21, 21])
        low = _series([9, 9, 8, 7, 19, 19, 8.5])  # last bar dips into [8, 11]
        close = _series([10, 10, 8, 20, 20, 20, 20])
        atr = _series([1, 1, 1, 1, 1, 1, 1])

        result = bullish_order_block_zones(open_, high, low, close, atr, displacement_atr_multiple=2.0)

        assert not pd.isna(result["zone_high"].iloc[5])  # still active before mitigation
        assert pd.isna(result["zone_high"].iloc[6])  # mitigated on the dip
        assert pd.isna(result["zone_low"].iloc[6])

    def test_zone_is_consumed_when_price_gaps_all_the_way_through_it(self):
        """A bar whose low trades BELOW the zone entirely (not just
        touching it) has consumed it just as much as a direct mitigation -
        must not stay marked active forever because the exact touch
        condition was never literally met."""
        open_ = _series([10, 10, 10, 8, 20, 20])
        high = _series([11, 11, 11, 9, 21, 21])
        low = _series([9, 9, 8, 7, 19, 5])  # last bar's low (5) is below zone_low (8)
        close = _series([10, 10, 8, 20, 20, 20])
        atr = _series([1, 1, 1, 1, 1, 1])

        result = bullish_order_block_zones(open_, high, low, close, atr, displacement_atr_multiple=2.0)

        assert pd.isna(result["zone_high"].iloc[5])

    def test_bearish_ob_is_a_mirror_of_bullish(self):
        # Bar 2 is bullish (close > open), bar 3 displaces down hard.
        open_ = _series([10, 10, 10, 12, 1])
        high = _series([11, 11, 13, 13, 2])
        low = _series([9, 9, 9, 0.5, 0.5])
        close = _series([10, 10, 12, 1, 1])
        atr = _series([1, 1, 1, 1, 1])

        result = bearish_order_block_zones(open_, high, low, close, atr, displacement_atr_multiple=2.0)

        assert result["bearish_ob"].iloc[2] == True  # noqa: E712
        assert result["zone_high"].iloc[3] == pytest.approx(high.iloc[2])
        assert result["zone_low"].iloc[3] == pytest.approx(low.iloc[2])

    def test_missing_or_zero_atr_never_forms_a_zone(self):
        """A missing/degenerate ATR must fail closed (no zone), not raise
        or divide by zero."""
        open_ = _series([10, 10, 10, 8, 20])
        high = _series([11, 11, 11, 9, 21])
        low = _series([9, 9, 8, 7, 19])
        close = _series([10, 10, 8, 20, 20])
        atr = _series([1, 1, float("nan"), 1, 1])

        result = bullish_order_block_zones(open_, high, low, close, atr, displacement_atr_multiple=2.0)

        assert not result["bullish_ob"].iloc[2]
