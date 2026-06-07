"""Market data time-gap detection helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from .validation import MarketDataSchema, validate_ohlcv_dataframe


@dataclass(frozen=True)
class MarketDataGap:
    """A missing timestamp inside an expected market data interval."""

    expected_timestamp: pd.Timestamp
    previous_timestamp: pd.Timestamp | None
    next_timestamp: pd.Timestamp | None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation of the gap."""

        return {
            "expected_timestamp": self.expected_timestamp.isoformat(),
            "previous_timestamp": (
                self.previous_timestamp.isoformat()
                if self.previous_timestamp is not None
                else None
            ),
            "next_timestamp": (
                self.next_timestamp.isoformat()
                if self.next_timestamp is not None
                else None
            ),
        }


def find_ohlcv_time_gaps(
    data: pd.DataFrame,
    *,
    expected_frequency: str = "D",
    schema: MarketDataSchema | None = None,
    timestamp_format: str | None = "ISO8601",
) -> list[MarketDataGap]:
    """Return missing timestamps for a validated OHLCV time series.

    The input is validated first so duplicate timestamps, invalid prices, and
    malformed timestamp values are rejected before interval gaps are evaluated.
    """

    schema = schema or MarketDataSchema()
    cleaned = validate_ohlcv_dataframe(
        data,
        schema=schema,
        timestamp_format=timestamp_format,
    )

    if len(cleaned) < 2:
        return []

    timestamps = pd.DatetimeIndex(cleaned[schema.timestamp])
    expected = pd.date_range(
        start=timestamps.min(),
        end=timestamps.max(),
        freq=expected_frequency,
    )

    missing_timestamps = expected.difference(timestamps)
    gaps: list[MarketDataGap] = []

    for missing in missing_timestamps:
        previous_candidates = timestamps[timestamps < missing]
        next_candidates = timestamps[timestamps > missing]

        previous = (
            previous_candidates.max()
            if len(previous_candidates) > 0
            else None
        )
        next_value = (
            next_candidates.min()
            if len(next_candidates) > 0
            else None
        )

        gaps.append(
            MarketDataGap(
                expected_timestamp=missing,
                previous_timestamp=previous,
                next_timestamp=next_value,
            )
        )

    return gaps
