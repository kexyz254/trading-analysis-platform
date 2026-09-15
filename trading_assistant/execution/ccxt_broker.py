"""CCXT implementation of the broker-neutral trading contract."""

from __future__ import annotations

from typing import Any, Dict


class CcxtBrokerAdapter:
    def __init__(self, exchange: Any):
        self.exchange = exchange

    def _ensure_markets(self) -> None:
        # CCXT's market(), amount_to_precision(), and price_to_precision()
        # require the exchange metadata cache to be populated first.
        # load_markets() is read-only and CCXT caches the result afterward.
        markets = getattr(self.exchange, "markets", None)
        if not markets:
            self.exchange.load_markets()

    def market(self, symbol: str) -> Dict[str, Any]:
        self._ensure_markets()
        return self.exchange.market(symbol)

    def fetch_balance(self) -> Dict[str, Any]:
        return self.exchange.fetch_balance()

    def fetch_ticker(self, symbol: str) -> Dict[str, Any]:
        return self.exchange.fetch_ticker(symbol)

    def normalize_amount(self, symbol: str, quantity: float) -> float:
        self._ensure_markets()
        return float(self.exchange.amount_to_precision(symbol, quantity))

    def normalize_price(self, symbol: str, price: float) -> float:
        self._ensure_markets()
        return float(self.exchange.price_to_precision(symbol, price))

    def submit_market_order(
        self, symbol: str, side: str, quantity: float,
        client_order_id: str,
    ) -> Dict[str, Any]:
        amount = self.normalize_amount(symbol, quantity)
        return self.exchange.create_order(
            symbol,
            "market",
            side,
            amount,
            None,
            {"newClientOrderId": client_order_id},
        )

    def submit_limit_order(
        self, symbol: str, side: str, quantity: float,
        price: float, client_order_id: str,
    ) -> Dict[str, Any]:
        amount = self.normalize_amount(symbol, quantity)
        normalized_price = self.normalize_price(symbol, price)
        return self.exchange.create_order(
            symbol,
            "limit",
            side,
            amount,
            normalized_price,
            {"newClientOrderId": client_order_id},
        )

    def cancel_order(self, symbol: str, order_id: str) -> Dict[str, Any]:
        return self.exchange.cancel_order(order_id, symbol)
