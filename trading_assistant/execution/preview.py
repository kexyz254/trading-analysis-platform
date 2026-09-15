"""Read-only pre-trade validation and order preview."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from trading_assistant.execution.broker import BrokerAdapter


@dataclass(frozen=True)
class OrderPreview:
    symbol: str
    side: str
    order_type: str
    requested_quantity: float
    quantity: float
    estimated_price: Optional[float]
    estimated_notional: Optional[float]
    spread_pct: Optional[float]
    base_currency: str
    quote_currency: str
    available_base: float
    available_quote: float
    min_amount: Optional[float]
    min_notional: Optional[float]
    approved: bool
    blockers: Tuple[str, ...]


class HumanOrderPreviewService:
    def __init__(self, broker: BrokerAdapter):
        self.broker = broker

    def preview(
        self, symbol: str, side: str, order_type: str,
        quantity: float, limit_price: Optional[float] = None,
    ) -> OrderPreview:
        side = side.lower()
        order_type = order_type.lower()

        if side not in {"buy", "sell"}:
            raise ValueError("side must be buy or sell")
        if order_type not in {"market", "limit"}:
            raise ValueError("order_type must be market or limit")
        if quantity <= 0:
            raise ValueError("quantity must be positive")

        market = self.broker.market(symbol)
        if not market or market.get("active") is False:
            raise ValueError("market is not active")

        base = str(market.get("base") or "")
        quote = str(market.get("quote") or "")
        requested_quantity = quantity
        quantity = self.broker.normalize_amount(symbol, quantity)

        ticker = self.broker.fetch_ticker(symbol)

        bid = ticker.get("bid")
        ask = ticker.get("ask")
        spread_pct = None
        try:
            bid_f = float(bid)
            ask_f = float(ask)
            mid = (bid_f + ask_f) / 2
            if bid_f > 0 and ask_f > 0 and mid > 0:
                spread_pct = (ask_f - bid_f) / mid
        except (TypeError, ValueError):
            spread_pct = None

        if order_type == "limit":
            if limit_price is None or limit_price <= 0:
                raise ValueError("positive limit_price required")
            price = self.broker.normalize_price(symbol, limit_price)
        else:
            price = ticker.get("ask") if side == "buy" else ticker.get("bid")
            price = float(price or ticker.get("last") or 0) or None

        balance = self.broker.fetch_balance()
        free = balance.get("free") or {}
        available_base = float(free.get(base) or 0)
        available_quote = float(free.get(quote) or 0)

        limits = market.get("limits") or {}
        min_amount = (limits.get("amount") or {}).get("min")
        min_notional = (limits.get("cost") or {}).get("min")

        notional = quantity * price if price is not None else None
        blockers = []

        if quantity <= 0:
            blockers.append("quantity rounds to zero")
        if min_amount is not None and quantity < float(min_amount):
            blockers.append("quantity below broker minimum")
        if price is None:
            blockers.append("current execution price unavailable")
        if min_notional is not None and notional is not None:
            if notional < float(min_notional):
                blockers.append("order value below broker minimum")

        if side == "buy" and notional is not None and notional > available_quote:
            blockers.append("insufficient quote balance")
        if side == "sell" and quantity > available_base:
            blockers.append("insufficient base balance")

        return OrderPreview(
            symbol=symbol, side=side, order_type=order_type,
            requested_quantity=requested_quantity, quantity=quantity,
            estimated_price=price, estimated_notional=notional,
            spread_pct=spread_pct,
            base_currency=base, quote_currency=quote,
            available_base=available_base, available_quote=available_quote,
            min_amount=float(min_amount) if min_amount is not None else None,
            min_notional=float(min_notional) if min_notional is not None else None,
            approved=not blockers, blockers=tuple(blockers),
        )
