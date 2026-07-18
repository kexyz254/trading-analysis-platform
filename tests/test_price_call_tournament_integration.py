"""
Integration tests: the price-call candidates inside the real tournament.

Verifies that price_call_5m / price_call_15m are first-class tournament
citizens - they appear in default_candidates(), the StrategyManager dispatches
them through backtest_price_call on the 15m feed, and a full run_tournament on
synthetic data produces valid fold metrics for them with no special-casing.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from trading_assistant.agents.strategy_manager import (
    StrategyManager,
    default_candidates,
)


# ------------------------------------------------------------------ helpers
def _hourly_ohlcv(n: int = 400, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    close = 100.0 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, size=n)))
    high = close * (1 + np.abs(rng.normal(0, 0.003, size=n)))
    low = close * (1 - np.abs(rng.normal(0, 0.003, size=n)))
    open_ = np.concatenate([[close[0]], close[:-1]])
    return pd.DataFrame(
        {
            "timestamp": idx,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": rng.uniform(50, 150, size=n),
        }
    )


def _fake_15m_feed(bars_15m: pd.DataFrame):
    class _Feed:
        def fetch_history(self, symbol: str, total_bars: int = 500) -> pd.DataFrame:
            return bars_15m.tail(total_bars)

    return _Feed()


def _synthetic_15m_for(hourly: pd.DataFrame, seed: int = 9) -> pd.DataFrame:
    """A 15m frame covering (and extending 30 days before) the hourly window,
    so the price-call 30-day training window has data to train on."""
    rng = np.random.default_rng(seed)
    start = hourly["timestamp"].iloc[0] - pd.Timedelta(days=31)
    end = hourly["timestamp"].iloc[-1] + pd.Timedelta(hours=1)
    idx = pd.date_range(start, end, freq="15min", tz="UTC")
    close = 100.0 * np.exp(np.cumsum(rng.normal(0.0001, 0.004, size=len(idx))))
    high = close * (1 + np.abs(rng.normal(0, 0.0015, size=len(idx))))
    low = close * (1 - np.abs(rng.normal(0, 0.0015, size=len(idx))))
    open_ = np.concatenate([[close[0]], close[:-1]])
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close,
         "volume": rng.uniform(10, 100, size=len(idx))},
        index=idx,
    )


# ------------------------------------------------------------------ tests
def test_price_call_candidates_registered():
    candidates = default_candidates()
    by_name = {c.name: c for c in candidates}
    assert "price_call_5m" in by_name
    assert "price_call_15m" in by_name
    assert by_name["price_call_5m"].kind == "ml_price_call"
    assert by_name["price_call_15m"].kind == "ml_price_call"
    # Horizons carried on params, not hardcoded.
    assert by_name["price_call_5m"].params["horizon_bars"] == 1
    assert by_name["price_call_15m"].params["horizon_bars"] == 3


def test_tournament_evaluates_price_call_candidates():
    hourly = _hourly_ohlcv()
    feed = _fake_15m_feed(_synthetic_15m_for(hourly))
    manager = StrategyManager(
        train_window_bars=120,
        test_window_bars=40,
        min_folds_required=1,
        feed_15m=feed,
    )
    result = manager.run_tournament("BTC/USDT", hourly)

    names = {c.name for c in result.candidates}
    assert "price_call_5m" in names
    assert "price_call_15m" in names

    for name in ("price_call_5m", "price_call_15m"):
        report = next(c for c in result.candidates if c.name == name)
        assert report.kind == "ml_price_call"
        assert len(report.folds) > 0
        for fold in report.folds:
            m = fold.metrics
            assert set(m.keys()) >= {
                "sharpe_ratio", "max_drawdown_pct", "win_rate",
                "num_trades", "total_return_pct", "profit_factor",
            }
            assert np.isfinite(m["sharpe_ratio"])
            assert m["num_trades"] >= 0


def test_price_call_without_feed_degrades_to_empty_metrics():
    """No 15m feed configured -> price-call folds are empty metrics, never a
    crash, so the tournament stays robust."""
    hourly = _hourly_ohlcv()
    manager = StrategyManager(
        train_window_bars=120, test_window_bars=40,
        min_folds_required=1, feed_15m=None,
    )
    result = manager.run_tournament("BTC/USDT", hourly)
    report = next(c for c in result.candidates if c.name == "price_call_5m")
    assert len(report.folds) > 0
    for fold in report.folds:
        assert fold.metrics["num_trades"] == 0
        assert fold.metrics["sharpe_ratio"] == 0.0
