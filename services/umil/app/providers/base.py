"""Provider protocols keep venue formats outside intelligence engines."""

from __future__ import annotations

from typing import Any, Protocol, Sequence


class PublicMarketProvider(Protocol):
    name: str

    def fetch_exchange_info(self) -> dict[str, Any]: ...

    def fetch_all_tickers(self) -> Sequence[dict[str, Any]]: ...

    def fetch_klines(
        self, symbol: str, interval: str, *, limit: int = 120,
    ) -> Sequence[Sequence[Any]]: ...

    def fetch_aggregate_trades(
        self, symbol: str, *, limit: int = 1000,
    ) -> Sequence[dict[str, Any]]: ...

    def fetch_depth(self, symbol: str, *, limit: int = 100) -> dict[str, Any]: ...


class ProviderError(RuntimeError):
    def __init__(self, provider: str, operation: str, reason: str) -> None:
        super().__init__(f"{provider} {operation} failed: {reason}")
        self.provider = provider
        self.operation = operation
        self.reason = reason
