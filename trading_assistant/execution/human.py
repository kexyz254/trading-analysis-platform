"""Explicit, audited human-authorized trading service."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Dict, Optional

from trading_assistant.execution.broker import BrokerAdapter
from trading_assistant.execution.provenance import HUMAN_PROVENANCE, TradeProvenance


class DuplicateOrderRequest(RuntimeError):
    pass


@dataclass(frozen=True)
class HumanOrderCommand:
    request_id: str
    account_id: str
    operator_id: str
    symbol: str
    side: str
    order_type: str
    quantity: float
    provenance: TradeProvenance
    confirmed: bool
    reference_price: Optional[float] = None
    limit_price: Optional[float] = None


@dataclass(frozen=True)
class HumanOrderReceipt:
    request_id: str
    symbol: str
    provenance: TradeProvenance
    broker_client_order_id: str
    broker_order: Dict[str, Any]


class HumanExecutionService:
    def __init__(self, broker: BrokerAdapter, journal: Any):
        self.broker = broker
        self.journal = journal

    @staticmethod
    def _client_id(request_id: str) -> str:
        digest = hashlib.sha256(request_id.encode()).hexdigest()[:24]
        return f"atsm-{digest}"

    def submit(self, command: HumanOrderCommand) -> HumanOrderReceipt:
        if not command.confirmed:
            raise PermissionError("explicit human confirmation required")
        if command.provenance not in HUMAN_PROVENANCE:
            raise PermissionError("human provenance required")
        if command.side.lower() not in {"buy", "sell"}:
            raise ValueError("side must be buy or sell")
        if command.order_type.lower() not in {"market", "limit"}:
            raise ValueError("order_type must be market or limit")
        if command.quantity <= 0:
            raise ValueError("quantity must be positive")

        market = self.broker.market(command.symbol)
        if not market or market.get("active") is False:
            raise ValueError("market is not active")

        limits = market.get("limits") or {}
        minimum = (limits.get("amount") or {}).get("min")
        if minimum is not None and command.quantity < float(minimum):
            raise ValueError("quantity below broker minimum")

        price = command.limit_price if command.order_type.lower() == "limit" else command.reference_price
        min_cost = (limits.get("cost") or {}).get("min")
        if min_cost is not None and price is not None:
            if command.quantity * price < float(min_cost):
                raise ValueError("order value below broker minimum")

        if command.order_type.lower() == "limit":
            if command.limit_price is None or command.limit_price <= 0:
                raise ValueError("positive limit_price required")

        client_id = self._client_id(command.request_id)

        claimed = self.journal.claim_manual_order_intent(
            request_id=command.request_id,
            account_id=command.account_id,
            operator_id=command.operator_id,
            symbol=command.symbol,
            side=command.side.lower(),
            order_type=command.order_type.lower(),
            quantity=command.quantity,
            reference_price=command.reference_price,
            limit_price=command.limit_price,
            provenance=command.provenance.value,
            broker_client_order_id=client_id,
        )
        if not claimed:
            raise DuplicateOrderRequest(command.request_id)

        self.journal.record_manual_order_event(
            request_id=command.request_id,
            event_type="SUBMITTING",
        )

        try:
            if command.order_type.lower() == "limit":
                order = self.broker.submit_limit_order(
                    command.symbol,
                    command.side.lower(),
                    command.quantity,
                    command.limit_price,
                    client_id,
                )
            else:
                order = self.broker.submit_market_order(
                    command.symbol,
                    command.side.lower(),
                    command.quantity,
                    client_id,
                )
        except Exception as exc:
            # A timeout may mean the broker accepted the order but our
            # response was lost. Never automatically retry this request.
            self.journal.record_manual_order_event(
                request_id=command.request_id,
                event_type="SUBMISSION_UNCERTAIN",
                details={"error": type(exc).__name__, "message": str(exc)},
            )
            raise

        broker_order_id = str(order.get("id")) if order.get("id") is not None else None

        self.journal.record_manual_order_event(
            request_id=command.request_id,
            event_type="ACCEPTED",
            broker_order_id=broker_order_id,
            details={
                key: order.get(key)
                for key in ("status", "filled", "remaining", "average", "price", "amount")
                if key in order
            },
        )

        return HumanOrderReceipt(
            request_id=command.request_id,
            symbol=command.symbol,
            provenance=command.provenance,
            broker_client_order_id=client_id,
            broker_order=order,
        )
