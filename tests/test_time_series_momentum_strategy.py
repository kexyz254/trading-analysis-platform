import pandas as pd

from trading_assistant.strategies.base import Signal
from trading_assistant.strategies.implementations import TimeSeriesMomentumStrategy


def _prices(values):
    return pd.Series(values, dtype=float)


def test_buys_when_trailing_return_clears_the_positive_threshold():
    strategy = TimeSeriesMomentumStrategy(lookback=5, min_abs_return=0.02)
    prices = _prices([100, 100, 100, 100, 100, 110])  # +10% over the lookback window

    assert strategy.generate_signal({"prices": prices}) == Signal.BUY


def test_sells_when_trailing_return_clears_the_negative_threshold():
    strategy = TimeSeriesMomentumStrategy(lookback=5, min_abs_return=0.02)
    prices = _prices([100, 100, 100, 100, 100, 90])  # -10% over the lookback window

    assert strategy.generate_signal({"prices": prices}) == Signal.SELL


def test_holds_in_the_dead_zone_around_a_near_flat_trailing_return():
    strategy = TimeSeriesMomentumStrategy(lookback=5, min_abs_return=0.02)
    prices = _prices([100, 100, 100, 100, 100, 100.5])  # +0.5%, inside the dead zone

    assert strategy.generate_signal({"prices": prices}) == Signal.HOLD


def test_holds_with_insufficient_history():
    strategy = TimeSeriesMomentumStrategy(lookback=20, min_abs_return=0.02)
    prices = _prices([100, 101, 102])

    assert strategy.generate_signal({"prices": prices}) == Signal.HOLD


def test_holds_when_prices_key_is_missing():
    strategy = TimeSeriesMomentumStrategy(lookback=5, min_abs_return=0.02)

    assert strategy.generate_signal({}) == Signal.HOLD


def test_time_series_momentum_is_registered_as_a_default_tournament_candidate():
    from trading_assistant.agents.strategy_manager import default_candidates

    names = [c.name for c in default_candidates()]
    assert "time_series_momentum" in names
