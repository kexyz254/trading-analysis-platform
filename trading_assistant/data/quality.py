"""Market data quality reporting helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from .validation import MarketDataSchema, validate_ohlcv_dataframe


@dataclass(frozen=True)
class MarketDataQualityReport:
    """Summary statistics for a validated OHLCV market data set."""

    row_count: int
    start_timestamp: pd.Timestamp | None
    end_timestamp: pd.Timestamp | None
    min_low: float | None
    max_high: float | None
    average_close: float | None
    total_volume: float

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation of the report."""

        return {
            "row_count": self.row_count,
            "start_timestamp": (
                self.start_timestamp.isoformat()
                if self.start_timestamp is not None
                else None
            ),
            "end_timestamp": (
                self.end_timestamp.isoformat()
                if self.end_timestamp is not None
                else None
            ),
            "min_low": self.min_low,
            "max_high": self.max_high,
            "average_close": self.average_close,
            "total_volume": self.total_volume,
        }


def analyze_ohlcv_quality(
    data: pd.DataFrame,
    *,
    schema: MarketDataSchema | None = None,
    timestamp_format: str | None = "ISO8601",
) -> MarketDataQualityReport:
    """Validate OHLCV data and return a compact quality summary."""

    schema = schema or MarketDataSchema()
    cleaned = validate_ohlcv_dataframe(
        data,
        schema=schema,
        timestamp_format=timestamp_format,
    )

    if cleaned.empty:
        return MarketDataQualityReport(
            row_count=0,
            start_timestamp=None,
            end_timestamp=None,
            min_low=None,
            max_high=None,
            average_close=None,
            total_volume=0.0,
        )

    return MarketDataQualityReport(
        row_count=len(cleaned),
        start_timestamp=cleaned[schema.timestamp].iloc[0],
        end_timestamp=cleaned[schema.timestamp].iloc[-1],
        min_low=float(cleaned[schema.low].min()),
        max_high=float(cleaned[schema.high].max()),
        average_close=float(cleaned[schema.close].mean()),
        total_volume=float(cleaned[schema.volume].sum()),
    )
