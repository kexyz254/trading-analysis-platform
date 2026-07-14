"""
15-minute OHLCV data feed for the ml_ensemble_15m strategy candidate.

This module provides a clean, exchange-agnostic interface for fetching
15‑minute klines, with optional local caching to reduce API calls during
backtesting and walk‑forward validation. It deliberately does NOT inherit
from the existing LiveFeed class — that class is tightly coupled to the
main 1h loop and its caching assumptions. A separate, minimal feed keeps
the 15‑m pipeline isolated and easy to test.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


class FifteenMinuteFeed:
    """
    Fetches 15‑minute OHLCV bars from the exchange.

    Parameters
    ----------
    exchange : ccxt.Exchange
        A pre-configured ccxt exchange instance (already sandboxed and
        authenticated). The feed never creates its own connection.
    cache_dir : str or Path, optional
        Directory for local CSV caches. If provided, fetched bars are
        written as <symbol>_15m.csv and reused on subsequent calls.
    """

    def __init__(self, exchange, cache_dir: Optional[Path] = None):
        self._exchange = exchange
        self._cache_dir = Path(cache_dir) if cache_dir else None
        if self._cache_dir:
            self._cache_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_history(self, symbol: str, total_bars: int = 500) -> pd.DataFrame:
        """
        Return up to `total_bars` of the most recent 15‑minute OHLCV bars.

        Columns: timestamp, open, high, low, close, volume.
        Timestamps are in UTC and set as the DataFrame index.
        """
        df = self._load_cache(symbol)
        if df is not None and len(df) >= total_bars:
            return df.iloc[-total_bars:]

        # Fetch from exchange
        since = None
        if df is not None and len(df) > 0:
            # Fetch only bars newer than the last cached bar
            since = int(df.index[-1].timestamp() * 1000)

        raw = self._exchange.fetch_ohlcv(
            symbol, timeframe="15m", limit=total_bars, since=since
        )

        new_df = self._raw_to_df(raw)
        if df is not None:
            df = pd.concat([df, new_df]).drop_duplicates().sort_index()
        else:
            df = new_df

        # Keep only the last total_bars rows
        df = df.iloc[-total_bars:]

        self._save_cache(symbol, df)
        return df

    def fetch_realtime(self, symbol: str) -> pd.DataFrame:
        """
        Fetch the most recent 1‑2 bars for live decision cycles.
        Returns a DataFrame with the same structure as fetch_history.
        """
        raw = self._exchange.fetch_ohlcv(symbol, timeframe="15m", limit=2)
        return self._raw_to_df(raw)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _raw_to_df(raw: list) -> pd.DataFrame:
        """Convert ccxt raw OHLCV list to DataFrame."""
        df = pd.DataFrame(
            raw,
            columns=["timestamp", "open", "high", "low", "close", "volume"],
        )
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df.set_index("timestamp", inplace=True)
        df.sort_index(inplace=True)
        return df

    def _cache_path(self, symbol: str) -> Path:
        """File path for the symbol's cache."""
        safe = symbol.replace("/", "_")
        return self._cache_dir / f"{safe}_15m.csv"

    def _load_cache(self, symbol: str) -> Optional[pd.DataFrame]:
        if not self._cache_dir:
            return None
        path = self._cache_path(symbol)
        if not path.exists():
            return None
        try:
            df = pd.read_csv(path, parse_dates=["timestamp"], index_col="timestamp")
            return df
        except Exception:
            logger.warning("Failed to read 15m cache for %s", symbol)
            return None

    def _save_cache(self, symbol: str, df: pd.DataFrame) -> None:
        if not self._cache_dir:
            return
        try:
            df.to_csv(self._cache_path(symbol))
        except Exception:
            logger.warning("Failed to write 15m cache for %s", symbol)