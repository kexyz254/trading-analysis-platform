from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from trading_assistant.market_data.integrity import (
    detect_anomalies,
    detect_extreme_move,
    detect_staleness,
    detect_structural_anomalies,
    detect_timestamp_anomalies,
)


def _clean_frame(n=20, start_price=100.0, start_ts="2026-01-01"):
    closes = [start_price + i for i in range(n)]
    return pd.DataFrame(
        {
            "timestamp": pd.date_range(start_ts, periods=n, freq="h", tz="UTC"),
            "open": closes,
            "high": [c + 1 for c in closes],
            "low": [c - 1 for c in closes],
            "close": closes,
            "volume": [100.0] * n,
        }
    )


class TestDetectStructuralAnomalies:
    def test_clean_frame_has_no_issues(self):
        assert detect_structural_anomalies(_clean_frame()) == []

    def test_empty_frame_has_no_issues(self):
        assert detect_structural_anomalies(pd.DataFrame()) == []

    def test_non_positive_price_detected(self):
        frame = _clean_frame()
        frame.loc[5, "close"] = 0.0
        issues = detect_structural_anomalies(frame)
        assert any("non-positive price" in i for i in issues)

    def test_negative_volume_detected(self):
        frame = _clean_frame()
        frame.loc[3, "volume"] = -10.0
        issues = detect_structural_anomalies(frame)
        assert any("negative volume" in i for i in issues)

    def test_high_below_low_detected(self):
        frame = _clean_frame()
        frame.loc[2, "high"] = 50.0
        frame.loc[2, "low"] = 60.0
        issues = detect_structural_anomalies(frame)
        assert any("high < low" in i for i in issues)

    def test_high_below_open_or_close_detected(self):
        frame = _clean_frame()
        frame.loc[4, "high"] = frame.loc[4, "close"] - 5  # high below close
        issues = detect_structural_anomalies(frame)
        assert any("high is below" in i for i in issues)

    def test_low_above_open_or_close_detected(self):
        frame = _clean_frame()
        frame.loc[4, "low"] = frame.loc[4, "close"] + 5  # low above close
        issues = detect_structural_anomalies(frame)
        assert any("low is above" in i for i in issues)

    def test_nan_in_close_detected(self):
        """Regression: NaN comparisons (<=, <, >, >=) always evaluate
        False in pandas - a malformed field from the exchange
        (feed.py's pd.to_numeric(..., errors="coerce") turns anything
        unparseable into NaN) used to silently pass every check here."""
        frame = _clean_frame()
        frame.loc[5, "close"] = float("nan")
        issues = detect_structural_anomalies(frame)
        assert any("NaN" in i for i in issues)

    def test_nan_in_volume_detected(self):
        frame = _clean_frame()
        frame.loc[5, "volume"] = float("nan")
        issues = detect_structural_anomalies(frame)
        assert any("NaN" in i for i in issues)


class TestDetectTimestampAnomalies:
    def test_clean_frame_has_no_issues(self):
        assert detect_timestamp_anomalies(_clean_frame()) == []

    def test_single_row_has_no_issues(self):
        assert detect_timestamp_anomalies(_clean_frame(n=1)) == []

    def test_out_of_order_timestamps_detected(self):
        frame = _clean_frame()
        frame.loc[3, "timestamp"], frame.loc[4, "timestamp"] = frame.loc[4, "timestamp"], frame.loc[3, "timestamp"]
        issues = detect_timestamp_anomalies(frame)
        assert any("not strictly increasing" in i for i in issues)

    def test_duplicate_timestamps_detected(self):
        frame = _clean_frame()
        frame.loc[5, "timestamp"] = frame.loc[4, "timestamp"]
        issues = detect_timestamp_anomalies(frame)
        assert any("duplicate timestamps" in i for i in issues)


class TestDetectExtremeMove:
    def test_normal_move_not_flagged(self):
        frame = _clean_frame()
        assert detect_extreme_move(frame) is None

    def test_extreme_move_flagged(self):
        frame = _clean_frame()
        frame.loc[frame.index[-1], "close"] = frame["close"].iloc[-2] * 3  # +200%
        issue = detect_extreme_move(frame)
        assert issue is not None
        assert "moved" in issue

    def test_single_row_frame_not_flagged(self):
        assert detect_extreme_move(_clean_frame(n=1)) is None

    def test_custom_threshold_respected(self):
        frame = _clean_frame()
        frame.loc[frame.index[-1], "close"] = frame["close"].iloc[-2] * 1.1  # +10%
        assert detect_extreme_move(frame, max_single_bar_move_pct=0.5) is None
        assert detect_extreme_move(frame, max_single_bar_move_pct=0.05) is not None

    def test_none_threshold_disables_the_check(self):
        frame = _clean_frame()
        frame.loc[frame.index[-1], "close"] = frame["close"].iloc[-2] * 10  # +900%
        assert detect_extreme_move(frame, max_single_bar_move_pct=None) is None


class TestDetectStaleness:
    def test_fresh_bar_not_flagged(self):
        frame = _clean_frame()
        now = pd.Timestamp(frame["timestamp"].iloc[-1]) + timedelta(minutes=1)
        assert detect_staleness(frame, timeframe_seconds=3600, now=now.to_pydatetime()) is None

    def test_stale_bar_flagged(self):
        frame = _clean_frame()
        now = pd.Timestamp(frame["timestamp"].iloc[-1]) + timedelta(hours=10)
        issue = detect_staleness(frame, timeframe_seconds=3600, now=now.to_pydatetime())
        assert issue is not None
        assert "old" in issue

    def test_within_staleness_multiple_not_flagged(self):
        frame = _clean_frame()
        now = pd.Timestamp(frame["timestamp"].iloc[-1]) + timedelta(hours=2, minutes=30)
        assert detect_staleness(frame, timeframe_seconds=3600, max_staleness_multiple=3.0, now=now.to_pydatetime()) is None

    def test_empty_frame_not_flagged(self):
        assert detect_staleness(pd.DataFrame(), timeframe_seconds=3600) is None


class TestDetectAnomalies:
    def test_clean_frame_returns_no_issues(self):
        frame = _clean_frame()
        now = pd.Timestamp(frame["timestamp"].iloc[-1]) + timedelta(minutes=1)
        assert detect_anomalies(frame, timeframe_seconds=3600, now=now.to_pydatetime()) == []

    def test_multiple_issues_all_reported(self):
        frame = _clean_frame()
        frame.loc[2, "close"] = -1.0  # structural
        frame.loc[frame.index[-1], "close"] = frame["close"].iloc[-2] * 5  # extreme move
        issues = detect_anomalies(frame)
        assert len(issues) >= 2

    def test_staleness_check_skipped_without_timeframe_seconds(self):
        frame = _clean_frame()
        issues = detect_anomalies(frame, timeframe_seconds=None)
        # no staleness-related issue even though "now" defaults to real wall clock,
        # far past this synthetic frame's 2026 timestamps
        assert not any("old" in i for i in issues)
