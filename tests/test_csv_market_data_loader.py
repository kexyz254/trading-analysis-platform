import pandas as pd
import pytest

from trading_assistant.data import load_ohlcv_csv
from trading_assistant.data.validation import MarketDataSchema, MarketDataValidationError


def test_load_ohlcv_csv_reads_valid_file_and_sorts_rows(tmp_path):
    path = tmp_path / "prices.csv"
    path.write_text(
        "\n".join(
            [
                "timestamp,open,high,low,close,volume",
                "2026-01-03,102,105,99,104,1200",
                "2026-01-01,100,103,98,102,1000",
                "2026-01-02,101,104,100,103,1100",
            ]
        ),
        encoding="utf-8",
    )

    result = load_ohlcv_csv(path)

    assert result["timestamp"].is_monotonic_increasing
    assert result["close"].tolist() == [102, 103, 104]
    assert str(result["timestamp"].dt.tz) == "UTC"


def test_load_ohlcv_csv_rejects_invalid_rows(tmp_path):
    path = tmp_path / "bad_prices.csv"
    path.write_text(
        "\n".join(
            [
                "timestamp,open,high,low,close,volume",
                "2026-01-01,100,90,98,102,1000",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(MarketDataValidationError, match="high price"):
        load_ohlcv_csv(path)


def test_load_ohlcv_csv_supports_custom_schema(tmp_path):
    path = tmp_path / "custom_prices.csv"
    path.write_text(
        "\n".join(
            [
                "date,o,h,l,c,v",
                "2026-01-01,100,110,95,105,1000",
            ]
        ),
        encoding="utf-8",
    )
    schema = MarketDataSchema(
        timestamp="date",
        open="o",
        high="h",
        low="l",
        close="c",
        volume="v",
    )

    result = load_ohlcv_csv(path, schema=schema)

    assert result.loc[0, "c"] == 105
    assert str(result["date"].dt.tz) == "UTC"


def test_load_ohlcv_csv_raises_for_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_ohlcv_csv(tmp_path / "missing.csv")


def test_load_ohlcv_csv_forwards_read_csv_options(tmp_path):
    path = tmp_path / "semicolon_prices.csv"
    path.write_text(
        "\n".join(
            [
                "timestamp;open;high;low;close;volume",
                "2026-01-01;100;110;95;105;1000",
            ]
        ),
        encoding="utf-8",
    )

    result = load_ohlcv_csv(path, read_csv_kwargs={"sep": ";"})

    assert result.loc[0, "close"] == 105
