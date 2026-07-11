import pandas as pd

from trading_assistant.strategies.base import Signal
from trading_assistant.strategies.implementations import SmartMoneyConceptsStrategy


def _series(values):
    return pd.Series(values, dtype=float)


def _data(open_, high, low, close):
    return {
        "open": _series(open_),
        "high": _series(high),
        "low": _series(low),
        "close": _series(close),
        "prices": _series(close),
    }


class TestSmartMoneyConceptsStrategy:
    def test_buys_on_a_fresh_bullish_continuation_bos(self):
        # Bullish structure (swing lows 5->7 ascending, swing highs
        # 15->20 ascending, lookback=1 - see test_smart_money_concepts.py
        # for the identical, independently-verified fixture), then a
        # final bar whose close decisively breaks above the last
        # confirmed swing high (20).
        low = [10, 5, 10, 10, 10, 7, 10, 10, 10, 99]
        high = [10, 10, 10, 15, 10, 10, 10, 20, 10, 101]
        open_ = [10] * 9 + [10]
        close = [10, 5, 10, 10, 10, 7, 10, 10, 10, 100]
        # atr_period explicitly small - the default (14) needs more bars
        # of history than this compact fixture has, even though this
        # particular test only cares about the BOS trigger, not order
        # blocks (the strategy always computes ATR regardless).
        strategy = SmartMoneyConceptsStrategy(swing_lookback=1, atr_period=3)

        signal = strategy.generate_signal(_data(open_, high, low, close))

        assert signal == Signal.BUY

    def test_sells_on_a_fresh_bearish_continuation_bos(self):
        low = [10, 8, 10, 10, 10, 5, 10, 10, 10, 2]
        high = [10, 10, 10, 20, 10, 10, 10, 15, 10, 12]
        open_ = [10] * 10
        close = [10, 8, 10, 10, 10, 5, 10, 10, 10, 3]
        # atr_period explicitly small - the default (14) needs more bars
        # of history than this compact, hand-verified fixture has.
        strategy = SmartMoneyConceptsStrategy(swing_lookback=1, atr_period=3)

        signal = strategy.generate_signal(_data(open_, high, low, close))

        assert signal == Signal.SELL

    def test_buys_on_a_bullish_order_block_retest_with_no_fresh_bos(self):
        """The second, distinct entry trigger: structure stays bullish,
        no breakout at the current bar, but price has pulled back into
        an active bullish order block zone formed earlier - empirically
        verified against trading_assistant.analysis.smart_money_concepts
        AND the real calculate_atr(period=3) directly before writing this
        assertion (see the module's own test file for the identical
        structure/OB fixture and computed intermediate values)."""
        open_ = [10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 8, 19]
        high = [10, 10, 10, 15, 10, 10, 10, 20, 10, 10.5, 19.2, 20]
        low = [10, 5, 10, 10, 10, 7, 10, 10, 10, 7.5, 7.8, 9]
        close = [10, 5, 10, 10, 10, 7, 10, 10, 10, 8, 19, 9]
        strategy = SmartMoneyConceptsStrategy(swing_lookback=1, displacement_atr_multiple=2.0, atr_period=3)

        signal = strategy.generate_signal(_data(open_, high, low, close))

        assert signal == Signal.BUY

    def test_holds_when_structure_is_ranging(self):
        # atr_period explicitly small - enough bars to satisfy swing
        # detection but not the default 14-period ATR requirement, which
        # would otherwise make this HOLD for "insufficient history"
        # rather than the genuinely-ranging structure this test targets.
        closes = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]  # monotonic - never forms a swing
        strategy = SmartMoneyConceptsStrategy(swing_lookback=2, atr_period=3)

        signal = strategy.generate_signal(_data(closes, [c + 1 for c in closes], [c - 1 for c in closes], closes))

        assert signal == Signal.HOLD

    def test_holds_on_a_bearish_reversal_mss_not_a_continuation(self):
        """MSS (structure reversing) is deliberately not a trade trigger -
        the design decision documented on the strategy's own docstring."""
        # Bearish structure, then break UP above the last confirmed swing
        # high - a reversal (MSS), not a continuation (BOS).
        low = [10, 8, 10, 10, 10, 5, 10, 10, 10, 99]
        high = [10, 10, 10, 20, 10, 10, 10, 15, 10, 101]
        open_ = [10] * 10
        close = [10, 8, 10, 10, 10, 5, 10, 10, 10, 100]
        strategy = SmartMoneyConceptsStrategy(swing_lookback=1, atr_period=3)

        signal = strategy.generate_signal(_data(open_, high, low, close))

        assert signal == Signal.HOLD

    def test_holds_with_insufficient_history(self):
        strategy = SmartMoneyConceptsStrategy(swing_lookback=5)
        closes = [1.0, 2.0, 3.0]

        signal = strategy.generate_signal(_data(closes, closes, closes, closes))

        assert signal == Signal.HOLD

    def test_holds_when_required_keys_are_missing(self):
        strategy = SmartMoneyConceptsStrategy()

        assert strategy.generate_signal({}) == Signal.HOLD
        assert strategy.generate_signal({"open": _series([1.0, 2.0])}) == Signal.HOLD


def test_smart_money_concepts_is_registered_as_a_default_tournament_candidate():
    from trading_assistant.agents.strategy_manager import default_candidates

    names = [c.name for c in default_candidates()]
    assert "smart_money_concepts" in names
