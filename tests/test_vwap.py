import numpy as np
import pandas as pd

from trading_assistant.analysis.vwap import session_vwap


def _frame(closes, volumes, start="2026-01-01 00:00", freq="h"):
    n = len(closes)
    ts = pd.date_range(start, periods=n, freq=freq)
    close = pd.Series(closes, dtype=float)
    return {
        "high": close,
        "low": close,
        "close": close,
        "volume": pd.Series(volumes, dtype=float),
        "timestamp": pd.Series(ts),
    }


class TestSessionVwap:
    def test_matches_expanding_mean_of_typical_price_under_equal_volume(self):
        # With every bar's volume identical, VWAP degenerates to a plain
        # cumulative average of typical price (=close here, since
        # high=low=close) - an independent, hand-verifiable ground truth.
        closes = [9.0, 11.0, 10.0, 19.0, 21.0, 20.0]
        data = _frame(closes, volumes=[100.0] * 6)

        vwap = session_vwap(**data)

        expected = pd.Series(closes).expanding().mean()
        pd.testing.assert_series_equal(vwap, expected, check_names=False)

    def test_resets_at_the_session_boundary_not_carrying_the_prior_session_forward(self):
        ts = pd.to_datetime(
            ["2026-01-01 22:00", "2026-01-01 23:00", "2026-01-02 00:00", "2026-01-02 01:00"]
        )
        closes = [100.0, 200.0, 5.0, 7.0]
        data = {
            "high": pd.Series(closes, dtype=float),
            "low": pd.Series(closes, dtype=float),
            "close": pd.Series(closes, dtype=float),
            "volume": pd.Series([10.0, 10.0, 10.0, 10.0]),
            "timestamp": pd.Series(ts),
        }

        vwap = session_vwap(**data, session_hours=24)

        assert vwap.tolist() == [100.0, 150.0, 5.0, 6.0]

    def test_weights_by_volume_not_just_price(self):
        # Two bars, same session: a huge low-volume spike should barely
        # move VWAP away from the dominant high-volume bar's price.
        ts = pd.date_range("2026-01-01 00:00", periods=2, freq="h")
        data = {
            "high": pd.Series([10.0, 1000.0]),
            "low": pd.Series([10.0, 1000.0]),
            "close": pd.Series([10.0, 1000.0]),
            "volume": pd.Series([1_000_000.0, 1.0]),
            "timestamp": pd.Series(ts),
        }

        vwap = session_vwap(**data)

        assert abs(vwap.iloc[-1] - 10.0) < 0.01

    def test_is_nan_where_cumulative_session_volume_is_zero(self):
        ts = pd.date_range("2026-01-01 00:00", periods=3, freq="h")
        data = {
            "high": pd.Series([10.0, 11.0, 12.0]),
            "low": pd.Series([10.0, 11.0, 12.0]),
            "close": pd.Series([10.0, 11.0, 12.0]),
            "volume": pd.Series([0.0, 0.0, 5.0]),
            "timestamp": pd.Series(ts),
        }

        vwap = session_vwap(**data)

        assert pd.isna(vwap.iloc[0])
        assert pd.isna(vwap.iloc[1])
        assert not pd.isna(vwap.iloc[2])

    def test_confirmation_is_causal_not_using_bars_from_the_future(self):
        closes = [9.0, 11.0, 10.0, 19.0, 21.0, 20.0, 30.0, 5.0]
        volumes = [100.0, 50.0, 75.0, 20.0, 200.0, 10.0, 5.0, 400.0]
        data = _frame(closes, volumes)

        full = session_vwap(**data)

        cutoff = 5
        truncated_data = {
            "high": data["high"].iloc[:cutoff],
            "low": data["low"].iloc[:cutoff],
            "close": data["close"].iloc[:cutoff],
            "volume": data["volume"].iloc[:cutoff],
            "timestamp": data["timestamp"].iloc[:cutoff],
        }
        truncated = session_vwap(**truncated_data)

        pd.testing.assert_series_equal(full.iloc[:cutoff], truncated, check_names=False)
