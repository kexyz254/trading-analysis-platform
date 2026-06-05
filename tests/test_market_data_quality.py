import pandas as pd
import pytest

from trading_assistant.data import analyze_ohlcv_quality
from trading_assistant.data.quality import MarketDataQualityReport
from trading_assistant.data.validation import MarketDataValidationError


def valid_market_data():
    return pd.DataFrame(
        {
            "timestamp": ["2026-01-03", "2026-01-01", "2026-01-02"],
            "open": [102.0, 100.0, 101.0],
            "high": [105.0, 103.0, 104.0],
            "low": [99.0, 98.0, 100.0],
            "close": [104.0, 102.0, 103.0],
            "volume": [1200, 1000, 1100],
        }
    )


def test_analyze_ohlcv_quality_returns_sorted_dataset_summary():
    report = analyze_ohlcv_quality(valid_market_data())

    assert report.row_count == 3
    assert report.start_timestamp.isoformat().startswith("2026-01-01")
    assert report.end_timestamp.isoformat().startswith("2026-01-03")
    assert report.min_low == 98.0
    assert report.max_high == 105.0
    assert report.average_close == 103.0
    assert report.total_volume == 3300.0


def test_market_data_quality_report_to_dict_is_json_friendly():
    report = analyze_ohlcv_quality(valid_market_data())

    payload = report.to_dict()

    assert payload["row_count"] == 3
    assert payload["start_timestamp"].startswith("2026-01-01")
    assert payload["end_timestamp"].startswith("2026-01-03")
    assert payload["total_volume"] == 3300.0


def test_analyze_ohlcv_quality_supports_explicit_timestamp_format():
    data = valid_market_data()
    data["timestamp"] = ["03/01/2026", "01/01/2026", "02/01/2026"]

    report = analyze_ohlcv_quality(data, timestamp_format="%d/%m/%Y")

    assert report.start_timestamp.isoformat().startswith("2026-01-01")
    assert report.end_timestamp.isoformat().startswith("2026-01-03")


def test_analyze_ohlcv_quality_returns_empty_report_for_empty_dataset():
    data = valid_market_data().iloc[0:0]

    report = analyze_ohlcv_quality(data)

    assert report == MarketDataQualityReport(
        row_count=0,
        start_timestamp=None,
        end_timestamp=None,
        min_low=None,
        max_high=None,
        average_close=None,
        total_volume=0.0,
    )


def test_analyze_ohlcv_quality_reuses_validator_errors():
    data = valid_market_data()
    data.loc[0, "high"] = 90.0

    with pytest.raises(MarketDataValidationError, match="high price"):
        analyze_ohlcv_quality(data)
