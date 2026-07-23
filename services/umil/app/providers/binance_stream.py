"""Live all-market mini-ticker stream with bounded fail-closed recovery."""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from .base import ProviderError


TickerCallback = Callable[[Sequence[dict[str, Any]]], Awaitable[None]]
StateCallback = Callable[[str, str | None], Awaitable[None]]


class BinanceAllMarketStream:
    name = "binance_spot_stream"
    url = "wss://stream.binance.com:9443/ws/!miniTicker@arr"

    def __init__(
        self,
        *,
        on_tickers: TickerCallback,
        on_state: StateCallback,
        max_message_bytes: int = 16 * 1024 * 1024,
    ) -> None:
        self.on_tickers = on_tickers
        self.on_state = on_state
        self.max_message_bytes = max_message_bytes
        self._ever_connected = False

    async def run(self, stop_event: asyncio.Event) -> None:
        failures = 0
        while not stop_event.is_set():
            await self.on_state("CONNECTING", None)
            try:
                await self._connection(stop_event)
                failures = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - recovery boundary owns all provider failures
                failures += 1
                reason = type(exc).__name__
                await self.on_state("RECOVERING" if self._ever_connected else "UNAVAILABLE", reason)
                delay = min(60.0, (2 ** min(failures, 6)) + random.uniform(0.0, 1.0))
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass

    async def _connection(self, stop_event: asyncio.Event) -> None:
        try:
            import websockets
        except ImportError as exc:  # pragma: no cover - deployment dependency gate
            raise ProviderError(self.name, "connect", "websockets dependency missing") from exc
        async with websockets.connect(
            self.url,
            max_size=self.max_message_bytes,
            open_timeout=10,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=5,
        ) as socket:
            self._ever_connected = True
            await self.on_state("LIVE", None)
            while not stop_event.is_set():
                try:
                    message = await asyncio.wait_for(socket.recv(), timeout=30.0)
                except asyncio.TimeoutError as exc:
                    raise ProviderError(self.name, "receive", "heartbeat timeout") from exc
                if not isinstance(message, (str, bytes)):
                    continue
                if len(message) > self.max_message_bytes:
                    raise ProviderError(self.name, "receive", "message exceeded safety limit")
                try:
                    payload = json.loads(message)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    await self.on_state("DEGRADED", "INVALID_JSON")
                    continue
                if not isinstance(payload, list):
                    await self.on_state("DEGRADED", "UNEXPECTED_SCHEMA")
                    continue
                rows = [row for row in payload if isinstance(row, dict)]
                if rows:
                    await self.on_tickers(rows)
