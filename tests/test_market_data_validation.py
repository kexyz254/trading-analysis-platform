import pandas as pd
import pytest

from trading_assistant.data.validation import (
    MarketDataSchema,
    MarketDataValidationError,
    validate_ohlcv_dataframe,
)


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


def test_validate_ohlcv_dataframe_sorts_and_normalizes_rows():
    cleaned = validate_ohlcv_dataframe(valid_market_data())

    assert cleaned["timestamp"].is_monotonic_increasing
    assert cleaned.index.tolist() == [0, 1, 2]
    assert str(cleaned["timestamp"].dt.tz) == "UTC"


def test_validate_ohlcv_dataframe_requires_all_columns():
    data = valid_market_data().drop(columns=["volume"])

    with pytest.raises(MarketDataValidationError, match="missing required"):
        validate_ohlcv_dataframe(data)


def test_validate_ohlcv_dataframe_rejects_invalid_timestamp():
    data = valid_market_data()
    data.loc[0, "timestamp"] = "not-a-date"

    with pytest.raises(MarketDataValidationError, match="timestamp"):
        validate_ohlcv_dataframe(data)


def test_validate_ohlcv_dataframe_rejects_duplicate_timestamps():
    data = valid_market_data()
    data.loc[0, "timestamp"] = data.loc[1, "timestamp"]

    with pytest.raises(MarketDataValidationError, match="duplicate"):
        validate_ohlcv_dataframe(data)


def test_validate_ohlcv_dataframe_rejects_non_numeric_prices():
    data = valid_market_data().astype({"close": "object"})
    data.loc[0, "close"] = "bad-price"

    with pytest.raises(MarketDataValidationError, match="numeric"):
        validate_ohlcv_dataframe(data)


def test_validate_ohlcv_dataframe_rejects_negative_volume():
    data = valid_market_data()
    data.loc[0, "volume"] = -1

    with pytest.raises(MarketDataValidationError, match="volume"):
        validate_ohlcv_dataframe(data)


def test_validate_ohlcv_dataframe_rejects_high_below_low():
    data = valid_market_data()
    data.loc[0, "high"] = 90.0

    with pytest.raises(MarketDataValidationError, match="high price"):
        validate_ohlcv_dataframe(data)


def test_validate_ohlcv_dataframe_rejects_close_outside_range():
    data = valid_market_data()
    data.loc[0, "close"] = 200.0

    with pytest.raises(MarketDataValidationError, match="close price"):
        validate_ohlcv_dataframe(data)


def test_validate_ohlcv_dataframe_supports_custom_schema():
    data = pd.DataFrame(
        {
            "date": ["2026-01-01"],
            "o": [100],
            "h": [110],
            "l": [95],
            "c": [105],
            "v": [1000],
        }
    )
    schema = MarketDataSchema(
        timestamp="date",
        open="o",
        high="h",
        low="l",
        close="c",
        volume="v",
    )

    cleaned = validate_ohlcv_dataframe(data, schema=schema)

    assert cleaned.loc[0, "c"] == 105


def test_validate_ohlcv_dataframe_supports_explicit_timestamp_format():
    data = valid_market_data()
    data["timestamp"] = ["03/01/2026", "01/01/2026", "02/01/2026"]

    cleaned = validate_ohlcv_dataframe(data, timestamp_format="%d/%m/%Y")

    assert cleaned["timestamp"].is_monotonic_increasing
    assert cleaned["close"].tolist() == [102.0, 103.0, 104.0]


def test_validate_ohlcv_dataframe_default_iso8601_avoids_inference_warning():
    import warnings

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        validate_ohlcv_dataframe(valid_market_data())

    messages = [str(item.message) for item in caught]
    assert not any("Could not infer format" in message for message in messages)
