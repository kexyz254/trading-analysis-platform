"""
Unit tests for the price-call ML candidate (Step 1 of the tournament wiring).

These verify the candidate in isolation, on synthetic 15m data:
  * make_price_call_targets produces correct directional labels
  * _train_pair trains two calibrated classifiers on a clean window
  * predict_live_probability returns calibrated up/down probs in [0, 1]
  * backtest_price_call returns the standard tournament metrics dict

No network, no real feed - a tiny in-memory fake feed stands in for
FifteenMinuteFeed so the fold logic is exercised deterministically.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from trading_assistant.strategies.price_call_candidate import (
    _empty_metrics,
    backtest_price_call,
    make_price_call_targets,
    predict_live_probability,
)


# ------------------------------------------------------------------ helpers
def _synthetic_15m(n: int = 800, seed: int = 7) -> pd.DataFrame:
    """A realistic-ish 15m OHLCV frame: gentle drift + mean-reverting noise so
    both directional classes appear and the features are non-degenerate."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC")
    # Random walk with small drift and occasional regime shifts.
    steps = rng.normal(0.0002, 0.004, size=n)
    steps[n // 3 : 2 * n // 3] -= 0.0006  # a down-regime in the middle
    close = 100.0 * np.exp(np.cumsum(steps))
    high = close * (1 + np.abs(rng.normal(0, 0.0015, size=n)))
    low = close * (1 - np.abs(rng.normal(0, 0.0015, size=n)))
    open_ = np.concatenate([[close[0]], close[:-1]])
    volume = rng.uniform(10, 100, size=n)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


class _FakeFeed:
    """Minimal stand-in for FifteenMinuteFeed.fetch_history."""

    def __init__(self, bars: pd.DataFrame):
        self._bars = bars

    def fetch_history(self, symbol: str, total_bars: int = 500) -> pd.DataFrame:
        return self._bars.tail(total_bars)


# ------------------------------------------------------------------ labels
def test_make_price_call_targets_direction():
    close = pd.Series([10.0, 11.0, 10.5, 10.5, 12.0])
    ohlcv = pd.DataFrame({"close": close})
    up, down = make_price_call_targets(ohlcv, horizon_bars=1)

    # bar0 -> bar1: 11 > 10  => up
    assert up.iloc[0] == 1.0 and down.iloc[0] == 0.0
    # bar1 -> bar2: 10.5 < 11 => down
    assert up.iloc[1] == 0.0 and down.iloc[1] == 1.0
    # bar2 -> bar3: equal     => neither (both 0)
    assert up.iloc[2] == 0.0 and down.iloc[2] == 0.0
    # bar3 -> bar4: 12 > 10.5 => up
    assert up.iloc[3] == 1.0 and down.iloc[3] == 0.0
    # last bar has no future
    assert np.isnan(up.iloc[-1]) and np.isnan(down.iloc[-1])


def test_make_price_call_targets_multibar_horizon():
    close = pd.Series(np.arange(10, 20, dtype=float))  # strictly increasing
    ohlcv = pd.DataFrame({"close": close})
    up, down = make_price_call_targets(ohlcv, horizon_bars=3)
    # every comparable bar rises over a 3-bar horizon
    assert (up.dropna() == 1.0).all()
    assert (down.dropna() == 0.0).all()
    # exactly the last 3 rows are NaN
    assert up.iloc[-3:].isna().all()
    assert up.iloc[:-3].notna().all()


# ------------------------------------------------------------------ training / live prediction
def test_predict_live_probability_returns_calibrated_probs():
    bars = _synthetic_15m()
    out = predict_live_probability(bars, {"horizon_bars": 1})
    assert out is not None
    for key in ("probability_up", "probability_down", "trained_rows"):
        assert key in out
    assert 0.0 <= out["probability_up"] <= 1.0
    assert 0.0 <= out["probability_down"] <= 1.0
    assert out["trained_rows"] > 0


def test_predict_live_probability_insufficient_data_returns_none():
    tiny = _synthetic_15m(n=20)
    assert predict_live_probability(tiny, {"horizon_bars": 1}) is None


# ------------------------------------------------------------------ backtest fold
def test_backtest_price_call_returns_valid_metrics():
    bars = _synthetic_15m(n=900)
    feed = _FakeFeed(bars)
    # Train on the first ~500 bars, test on the next ~200.
    train_start = bars.index[200]
    train_end = bars.index[600]
    test_start = bars.index[600]
    test_end = bars.index[800]

    metrics = backtest_price_call(
        "BTC/USDT", feed, train_start, train_end, test_start, test_end,
        {"horizon_bars": 1},
    )

    assert set(metrics.keys()) == set(_empty_metrics().keys())
    assert isinstance(metrics["num_trades"], (int, np.integer))
    assert metrics["num_trades"] >= 0
    # Sharpe / drawdown / return are finite real numbers.
    for key in ("sharpe_ratio", "max_drawdown_pct", "total_return_pct", "win_rate"):
        assert np.isfinite(metrics[key])
    # profit_factor may be inf (no losses) but never NaN.
    assert not np.isnan(metrics["profit_factor"])


def test_backtest_price_call_empty_feed_degrades_gracefully():
    feed = _FakeFeed(pd.DataFrame(columns=["open", "high", "low", "close", "volume"]))
    ts = pd.Timestamp("2024-01-01", tz="UTC")
    metrics = backtest_price_call(
        "BTC/USDT", feed, ts, ts + pd.Timedelta(hours=1),
        ts + pd.Timedelta(hours=1), ts + pd.Timedelta(hours=2), {"horizon_bars": 1},
    )
    assert metrics == _empty_metrics()
