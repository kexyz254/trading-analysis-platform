"""CSV loading helpers for market data research workflows."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from .validation import MarketDataSchema, validate_ohlcv_dataframe


def load_ohlcv_csv(
    path: str | Path,
    *,
    schema: MarketDataSchema | None = None,
    sort: bool = True,
    read_csv_kwargs: dict[str, Any] | None = None,
) -> pd.DataFrame:
    """Load OHLCV market data from CSV and validate it.

    Parameters
    ----------
    path:
        CSV file path containing OHLCV-style market data.
    schema:
        Optional schema describing the timestamp, OHLC, and volume columns.
    sort:
        Whether to sort the cleaned frame by timestamp.
    read_csv_kwargs:
        Optional keyword arguments forwarded to pandas.read_csv.
    """

    csv_path = Path(path)
    if not csv_path.exists():
        raise FileNotFoundError(f"market data CSV does not exist: {csv_path}")

    options = dict(read_csv_kwargs or {})
    raw = pd.read_csv(csv_path, **options)
    return validate_ohlcv_dataframe(raw, schema=schema, sort=sort)
