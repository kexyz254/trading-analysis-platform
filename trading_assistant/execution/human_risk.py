"""Risk policy applied to explicitly human-authorized orders."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from trading_assistant.execution.preview import OrderPreview


@dataclass(frozen=True)
class HumanRiskPolicy:
    # Optional hard limits. None means the user/account has not configured
    # that particular ceiling yet.
    max_order_notional: Optional[float] = None
    max_balance_usage_pct: Optional[float] = None
    max_spread_pct: Optional[float] = None


@dataclass(frozen=True)
class HumanRiskDecision:
    approved: bool
    blockers: Tuple[str, ...]
    warnings: Tuple[str, ...]
    balance_usage_pct: Optional[float]
    spread_pct: Optional[float]


def evaluate_human_order_risk(
    preview: OrderPreview,
    policy: HumanRiskPolicy,
) -> HumanRiskDecision:
    blockers = list(preview.blockers)
    warnings = []

    balance_usage_pct = None
    if (
        preview.side == "buy"
        and preview.estimated_notional is not None
        and preview.available_quote > 0
    ):
        balance_usage_pct = preview.estimated_notional / preview.available_quote

    spread_pct = getattr(preview, "spread_pct", None)

    if (
        policy.max_order_notional is not None
        and preview.estimated_notional is not None
        and preview.estimated_notional > policy.max_order_notional
    ):
        blockers.append("order exceeds configured maximum notional")

    if (
        policy.max_balance_usage_pct is not None
        and balance_usage_pct is not None
        and balance_usage_pct > policy.max_balance_usage_pct
    ):
        blockers.append("order exceeds configured balance-usage limit")

    if (
        policy.max_spread_pct is not None
        and spread_pct is not None
        and spread_pct > policy.max_spread_pct
    ):
        blockers.append("current spread exceeds configured limit")

    if balance_usage_pct is not None:
        if balance_usage_pct >= 0.5:
            warnings.append("order uses at least 50% of currently available quote balance")
        elif balance_usage_pct >= 0.25:
            warnings.append("order uses at least 25% of currently available quote balance")

    return HumanRiskDecision(
        approved=not blockers,
        blockers=tuple(dict.fromkeys(blockers)),
        warnings=tuple(warnings),
        balance_usage_pct=balance_usage_pct,
        spread_pct=spread_pct,
    )
