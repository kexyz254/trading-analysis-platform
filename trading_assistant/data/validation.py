"""Market data validation utilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import pandas as pd


class MarketDataValidationError(ValueError):
    """Raised when market data violates the expected OHLCV schema."""


@dataclass(frozen=True)
class MarketDataSchema:
    """Column names expected in an OHLCV-style market data frame."""

    timestamp: str = "timestamp"
    open: str = "open"
    high: str = "high"
    low: str = "low"
    close: str = "close"
    volume: str = "volume"

    @property
    def price_columns(self) -> tuple[str, str, str, str]:
        return (self.open, self.high, self.low, self.close)

    @property
    def required_columns(self) -> tuple[str, str, str, str, str, str]:
        return (
            self.timestamp,
            self.open,
            self.high,
            self.low,
            self.close,
            self.volume,
        )


def _missing_columns(columns: Iterable[str], required: Iterable[str]) -> list[str]:
    available = set(columns)
    return [column for column in required if column not in available]


def validate_ohlcv_dataframe(
    data: pd.DataFrame,
    *,
    schema: MarketDataSchema | None = None,
    sort: bool = True,
) -> pd.DataFrame:
    """Validate and normalize an OHLCV market data frame.

    The returned frame is a copy of the input with parsed timestamps, numeric
    OHLCV columns, deterministic ordering, and a clean integer index.
    """

    schema = schema or MarketDataSchema()

    if not isinstance(data, pd.DataFrame):
        raise MarketDataValidationError("market data must be a pandas DataFrame")

    missing = _missing_columns(data.columns, schema.required_columns)
    if missing:
        raise MarketDataValidationError(
            f"missing required market data columns: {', '.join(missing)}"
        )

    cleaned = data.copy()

    cleaned[schema.timestamp] = pd.to_datetime(
        cleaned[schema.timestamp],
        errors="coerce",
        utc=True,
    )

    if cleaned[schema.timestamp].isna().any():
        raise MarketDataValidationError("timestamp column contains invalid values")

    if cleaned[schema.timestamp].duplicated().any():
        raise MarketDataValidationError("timestamp column contains duplicate values")

    numeric_columns = (*schema.price_columns, schema.volume)
    for column in numeric_columns:
        cleaned[column] = pd.to_numeric(cleaned[column], errors="coerce")

    if cleaned[list(numeric_columns)].isna().any().any():
        raise MarketDataValidationError("OHLCV columns must contain numeric values")

    if (cleaned[list(schema.price_columns)] < 0).any().any():
        raise MarketDataValidationError("price columns cannot contain negative values")

    if (cleaned[schema.volume] < 0).any():
        raise MarketDataValidationError("volume column cannot contain negative values")

    if (cleaned[schema.high] < cleaned[schema.low]).any():
        raise MarketDataValidationError("high price cannot be lower than low price")

    if (cleaned[schema.open] > cleaned[schema.high]).any():
        raise MarketDataValidationError("open price cannot be greater than high price")

    if (cleaned[schema.open] < cleaned[schema.low]).any():
        raise MarketDataValidationError("open price cannot be lower than low price")

    if (cleaned[schema.close] > cleaned[schema.high]).any():
        raise MarketDataValidationError("close price cannot be greater than high price")

    if (cleaned[schema.close] < cleaned[schema.low]).any():
        raise MarketDataValidationError("close price cannot be lower than low price")

    if sort:
        cleaned = cleaned.sort_values(schema.timestamp, kind="mergesort")

    return cleaned.reset_index(drop=True)
