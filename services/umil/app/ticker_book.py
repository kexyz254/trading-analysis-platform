"""Concurrency-safe latest-value projection for all market tickers."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Iterable

from .contracts import MarketTicker


class LiveTickerBook:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tickers: dict[str, MarketTicker] = {}
        self._last_batch_at: datetime | None = None
        self._out_of_order_events = 0

    def apply(self, tickers: Iterable[MarketTicker], *, received_at: datetime | None = None) -> int:
        received_at = received_at or datetime.now(timezone.utc)
        accepted = 0
        with self._lock:
            for ticker in tickers:
                current = self._tickers.get(ticker.instrument_id)
                if current is not None and ticker.event_time < current.event_time:
                    self._out_of_order_events += 1
                    continue
                self._tickers[ticker.instrument_id] = ticker
                accepted += 1
            if accepted:
                self._last_batch_at = received_at
        return accepted

    def snapshot(self) -> tuple[MarketTicker, ...]:
        with self._lock:
            return tuple(sorted(self._tickers.values(), key=lambda row: row.instrument_id))

    @property
    def last_batch_at(self) -> datetime | None:
        with self._lock:
            return self._last_batch_at

    @property
    def out_of_order_events(self) -> int:
        with self._lock:
            return self._out_of_order_events
