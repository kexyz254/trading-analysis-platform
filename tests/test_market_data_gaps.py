import pandas as pd
import pytest

from trading_assistant.data import find_ohlcv_time_gaps
from trading_assistant.data.gaps import MarketDataGap
from trading_assistant.data.validation import MarketDataValidationError


def market_data_with_gap():
    return pd.DataFrame(
        {
            "timestamp": ["2026-01-01", "2026-01-03", "2026-01-04"],
            "open": [100.0, 102.0, 103.0],
            "high": [103.0, 105.0, 106.0],
            "low": [98.0, 99.0, 100.0],
            "close": [102.0, 104.0, 105.0],
            "volume": [1000, 1200, 1300],
        }
    )


def test_find_ohlcv_time_gaps_detects_missing_daily_timestamp():
    gaps = find_ohlcv_time_gaps(market_data_with_gap(), expected_frequency="D")

    assert len(gaps) == 1
    assert isinstance(gaps[0], MarketDataGap)
    assert gaps[0].expected_timestamp.isoformat().startswith("2026-01-02")
    assert gaps[0].previous_timestamp.isoformat().startswith("2026-01-01")
    assert gaps[0].next_timestamp.isoformat().startswith("2026-01-03")


def test_find_ohlcv_time_gaps_returns_empty_list_for_complete_series():
    data = pd.DataFrame(
        {
            "timestamp": ["2026-01-01", "2026-01-02", "2026-01-03"],
            "open": [100.0, 101.0, 102.0],
            "high": [103.0, 104.0, 105.0],
            "low": [98.0, 99.0, 100.0],
            "close": [102.0, 103.0, 104.0],
            "volume": [1000, 1100, 1200],
        }
    )

    assert find_ohlcv_time_gaps(data, expected_frequency="D") == []


def test_find_ohlcv_time_gaps_supports_explicit_timestamp_format():
    data = market_data_with_gap()
    data["timestamp"] = ["01/01/2026", "03/01/2026", "04/01/2026"]

    gaps = find_ohlcv_time_gaps(
        data,
        expected_frequency="D",
        timestamp_format="%d/%m/%Y",
    )

    assert len(gaps) == 1
    assert gaps[0].expected_timestamp.isoformat().startswith("2026-01-02")


def test_market_data_gap_to_dict_is_json_friendly():
    gap = find_ohlcv_time_gaps(market_data_with_gap())[0]

    payload = gap.to_dict()

    assert payload["expected_timestamp"].startswith("2026-01-02")
    assert payload["previous_timestamp"].startswith("2026-01-01")
    assert payload["next_timestamp"].startswith("2026-01-03")


def test_find_ohlcv_time_gaps_reuses_validator_errors():
    data = market_data_with_gap()
    data.loc[0, "high"] = 90.0

    with pytest.raises(MarketDataValidationError, match="high price"):
        find_ohlcv_time_gaps(data)


def test_find_ohlcv_time_gaps_returns_empty_for_single_row():
    data = market_data_with_gap().iloc[[0]]

    assert find_ohlcv_time_gaps(data) == []


def test_summarize_ohlcv_time_gaps_reports_gap_count_and_bounds():
    from trading_assistant.data import summarize_ohlcv_time_gaps

    gaps = summarize_ohlcv_time_gaps(market_data_with_gap())

    assert gaps.gap_count == 1
    assert gaps.first_gap.isoformat().startswith("2026-01-02")
    assert gaps.last_gap.isoformat().startswith("2026-01-02")
    assert len(gaps.missing_timestamps) == 1


def test_gap_summary_to_dict_is_json_friendly():
    from trading_assistant.data import summarize_ohlcv_time_gaps

    summary = summarize_ohlcv_time_gaps(market_data_with_gap())
    payload = summary.to_dict()

    assert payload["gap_count"] == 1
    assert payload["first_gap"].startswith("2026-01-02")
    assert payload["last_gap"].startswith("2026-01-02")
    assert payload["missing_timestamps"][0].startswith("2026-01-02")


def test_summarize_ohlcv_time_gaps_returns_empty_summary_for_complete_series():
    from trading_assistant.data import summarize_ohlcv_time_gaps

    data = pd.DataFrame(
        {
            "timestamp": ["2026-01-01", "2026-01-02", "2026-01-03"],
            "open": [100.0, 101.0, 102.0],
            "high": [103.0, 104.0, 105.0],
            "low": [98.0, 99.0, 100.0],
            "close": [102.0, 103.0, 104.0],
            "volume": [1000, 1100, 1200],
        }
    )

    summary = summarize_ohlcv_time_gaps(data)

    assert summary.gap_count == 0
    assert summary.first_gap is None
    assert summary.last_gap is None
    assert summary.missing_timestamps == ()
