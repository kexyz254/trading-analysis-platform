"""Two-stage human trading gateway: prepare first, execute explicitly second."""

from __future__ import annotations

from dataclasses import dataclass

from trading_assistant.execution.human import (
    HumanExecutionService,
    HumanOrderCommand,
    HumanOrderReceipt,
)
from trading_assistant.execution.human_risk import (
    HumanRiskDecision,
    HumanRiskPolicy,
    evaluate_human_order_risk,
)
from trading_assistant.execution.preview import (
    HumanOrderPreviewService,
    OrderPreview,
)


@dataclass(frozen=True)
class PreparedHumanOrder:
    preview: OrderPreview
    risk: HumanRiskDecision


class HumanOrderGateway:
    def __init__(
        self,
        preview_service: HumanOrderPreviewService,
        execution_service: HumanExecutionService,
        risk_policy: HumanRiskPolicy,
    ):
        self.preview_service = preview_service
        self.execution_service = execution_service
        self.risk_policy = risk_policy

    def prepare(
        self,
        *,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float,
        limit_price=None,
    ) -> PreparedHumanOrder:
        preview = self.preview_service.preview(
            symbol,
            side,
            order_type,
            quantity,
            limit_price=limit_price,
        )

        risk = evaluate_human_order_risk(
            preview,
            self.risk_policy,
        )

        return PreparedHumanOrder(
            preview=preview,
            risk=risk,
        )

    def submit(
        self,
        command: HumanOrderCommand,
    ) -> HumanOrderReceipt:
        # Re-preview at submission time. UI previews can become stale;
        # broker price, spread and balances are authoritative now.
        prepared = self.prepare(
            symbol=command.symbol,
            side=command.side,
            order_type=command.order_type,
            quantity=command.quantity,
            limit_price=command.limit_price,
        )

        if not prepared.risk.approved:
            raise PermissionError(
                "manual order blocked by current pre-trade risk state: "
                + "; ".join(prepared.risk.blockers)
            )

        return self.execution_service.submit(command)
