import pandas as pd

from trading_assistant.strategies.base import Signal
from trading_assistant.strategies.implementations import VWAPReversionStrategy


def _data(closes, volumes, start="2026-01-01 00:00"):
    ts = pd.date_range(start, periods=len(closes), freq="h")
    close = pd.Series(closes, dtype=float)
    return {
        "high": close,
        "low": close,
        "close": close,
        "volume": pd.Series(volumes, dtype=float),
        "timestamp": pd.Series(ts),
    }


class TestVWAPReversionStrategy:
    def test_buys_when_price_is_stretched_below_session_vwap(self):
        # Session VWAP after 4 equal-volume bars at 100 sits at 100; the
        # 5th bar dropping to 90 is 10% below it - well past the 2%
        # default threshold - empirically verified before writing this
        # assertion (see the direct execution used during development).
        data = _data([100.0, 100.0, 100.0, 100.0, 90.0], volumes=[10.0] * 5)
        strategy = VWAPReversionStrategy(threshold_pct=0.02)

        assert strategy.generate_signal(data) == Signal.BUY

    def test_sells_when_price_is_stretched_above_session_vwap(self):
        data = _data([100.0, 100.0, 100.0, 100.0, 110.0], volumes=[10.0] * 5)
        strategy = VWAPReversionStrategy(threshold_pct=0.02)

        assert strategy.generate_signal(data) == Signal.SELL

    def test_holds_within_the_threshold_band(self):
        data = _data([100.0, 100.0, 100.0, 100.0, 100.5], volumes=[10.0] * 5)
        strategy = VWAPReversionStrategy(threshold_pct=0.02)

        assert strategy.generate_signal(data) == Signal.HOLD

    def test_holds_with_insufficient_history(self):
        data = _data([100.0], volumes=[10.0])
        strategy = VWAPReversionStrategy()

        assert strategy.generate_signal(data) == Signal.HOLD

    def test_holds_when_required_keys_are_missing(self):
        strategy = VWAPReversionStrategy()

        assert strategy.generate_signal({}) == Signal.HOLD
        assert strategy.generate_signal({"close": pd.Series([1.0, 2.0])}) == Signal.HOLD

    def test_holds_when_cumulative_session_volume_is_zero(self):
        # No trades yet this session to weight by - session_vwap returns
        # NaN, which must not be misread as "infinitely far below vwap".
        data = _data([100.0, 90.0], volumes=[0.0, 0.0])
        strategy = VWAPReversionStrategy()

        assert strategy.generate_signal(data) == Signal.HOLD

    def test_a_fresh_session_resets_the_reference_point(self):
        # Day 1 ends at a high price level; day 2 opens far lower but
        # right at ITS OWN session's (single-bar) vwap - not stretched
        # relative to the new session, so this must NOT fire BUY just
        # because it's far below day 1's price level.
        ts = pd.to_datetime(["2026-01-01 22:00", "2026-01-01 23:00", "2026-01-02 00:00"])
        closes = [1000.0, 1000.0, 10.0]
        data = {
            "high": pd.Series(closes),
            "low": pd.Series(closes),
            "close": pd.Series(closes),
            "volume": pd.Series([10.0, 10.0, 10.0]),
            "timestamp": pd.Series(ts),
        }
        strategy = VWAPReversionStrategy(threshold_pct=0.02)

        assert strategy.generate_signal(data) == Signal.HOLD


def test_vwap_reversion_is_registered_as_a_default_tournament_candidate():
    from trading_assistant.agents.strategy_manager import default_candidates

    names = [c.name for c in default_candidates()]
    assert "vwap_reversion" in names
