"""Broker-neutral contract used by trading services."""

from __future__ import annotations

from typing import Any, Dict, Protocol


class BrokerAdapter(Protocol):
    def market(self, symbol: str) -> Dict[str, Any]: ...
    def fetch_balance(self) -> Dict[str, Any]: ...
    def fetch_ticker(self, symbol: str) -> Dict[str, Any]: ...
    def normalize_amount(self, symbol: str, quantity: float) -> float: ...
    def normalize_price(self, symbol: str, price: float) -> float: ...

    def submit_market_order(
        self, symbol: str, side: str, quantity: float,
        client_order_id: str,
    ) -> Dict[str, Any]: ...

    def submit_limit_order(
        self, symbol: str, side: str, quantity: float,
        price: float, client_order_id: str,
    ) -> Dict[str, Any]: ...

    def cancel_order(
        self, symbol: str, order_id: str,
    ) -> Dict[str, Any]: ...
