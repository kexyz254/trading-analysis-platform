"""Authority-domain ownership for orders and positions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from trading_assistant.execution.provenance import TradeProvenance


@dataclass(frozen=True)
class OrderOwnership:
    account_id: str
    symbol: str
    side: str
    quantity: float
    provenance: TradeProvenance
    broker_order_id: Optional[str] = None
    broker_client_order_id: Optional[str] = None
    request_id: Optional[str] = None
    operator_id: Optional[str] = None
    strategy_id: Optional[str] = None
    status: str = "accepted"


class OwnershipService:
    def __init__(self, journal):
        self.journal = journal

    def record_order(self, ownership: OrderOwnership) -> int:
        if ownership.quantity <= 0:
            raise ValueError("ownership quantity must be positive")

        return self.journal.record_execution_ownership(
            account_id=ownership.account_id,
            symbol=ownership.symbol,
            side=ownership.side.lower(),
            quantity=ownership.quantity,
            provenance=ownership.provenance.value,
            status=ownership.status,
            broker_order_id=ownership.broker_order_id,
            broker_client_order_id=ownership.broker_client_order_id,
            request_id=ownership.request_id,
            operator_id=ownership.operator_id,
            strategy_id=ownership.strategy_id,
        )

    def record_fill(
        self, *, account_id: str, symbol: str,
        provenance: TradeProvenance, side: str,
        quantity: float, price: float,
        broker_order_id: Optional[str] = None,
        request_id: Optional[str] = None,
        realized_pnl: Optional[float] = None,
    ) -> int:
        event_type = (
            "POSITION_INCREASE"
            if side.lower() == "buy"
            else "POSITION_DECREASE"
        )

        return self.journal.record_position_ownership_event(
            account_id=account_id,
            symbol=symbol,
            provenance=provenance.value,
            event_type=event_type,
            quantity=quantity,
            price=price,
            realized_pnl=realized_pnl,
            broker_order_id=broker_order_id,
            request_id=request_id,
        )
