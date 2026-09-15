"""Replay ownership events into manual/ATS portfolio accounting."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, Mapping, Tuple


MANUAL_PROVENANCE = {"MANUAL", "ATS_ASSISTED_MANUAL"}
ATS_PROVENANCE = {"ATS_AUTONOMOUS"}
UNATTRIBUTED_PROVENANCE = {"EXTERNAL", "UNKNOWN"}


@dataclass
class PositionState:
    symbol: str
    provenance: str
    quantity: float = 0.0
    average_cost: float = 0.0
    realized_pnl: float = 0.0
    fees: float = 0.0


@dataclass(frozen=True)
class AccountingBucket:
    equity: float
    market_value: float
    cost_basis: float
    realized_pnl: float
    unrealized_pnl: float
    fees: float


@dataclass(frozen=True)
class OwnershipAccountingSummary:
    manual: AccountingBucket
    ats: AccountingBucket
    unattributed: AccountingBucket
    positions: Tuple[PositionState, ...]
    warnings: Tuple[str, ...]


def _fee(event: dict) -> float:
    try:
        details = json.loads(event.get("details") or "{}")
        return float(details.get("fee") or 0.0)
    except Exception:
        return 0.0


def replay_owned_positions(events) -> tuple[Dict[tuple, PositionState], list[str]]:
    states: Dict[tuple, PositionState] = {}
    warnings = []

    for event in events:
        symbol = str(event["symbol"])
        provenance = str(event["provenance"])
        key = (provenance, symbol)

        state = states.setdefault(
            key,
            PositionState(symbol=symbol, provenance=provenance),
        )

        quantity = float(event["quantity"])
        price = float(event["price"] or 0)
        fee = _fee(event)

        if quantity <= 0:
            warnings.append(
                f"{provenance}:{symbol}: non-positive ownership event quantity"
            )
            continue

        if event["event_type"] == "POSITION_INCREASE":
            old_cost = state.quantity * state.average_cost
            new_cost = quantity * price

            state.quantity += quantity
            if state.quantity > 0:
                state.average_cost = (
                    old_cost + new_cost + fee
                ) / state.quantity

            state.fees += fee

        elif event["event_type"] == "POSITION_DECREASE":
            removable = min(quantity, state.quantity)

            explicit_pnl = event.get("realized_pnl")
            if explicit_pnl is not None:
                pnl = float(explicit_pnl)
            else:
                pnl = (
                    (price - state.average_cost) * removable
                    - fee
                )

            state.realized_pnl += pnl
            state.fees += fee
            state.quantity -= removable

            if quantity > removable:
                warnings.append(
                    f"{provenance}:{symbol}: sell quantity exceeded "
                    "owned quantity"
                )

            if state.quantity <= 1e-15:
                state.quantity = 0.0
                state.average_cost = 0.0

        else:
            warnings.append(
                f"{provenance}:{symbol}: unknown event "
                f"{event['event_type']}"
            )

    return states, warnings


def _bucket(
    states,
    provenances,
    mark_prices: Mapping[str, float],
) -> AccountingBucket:
    market_value = 0.0
    cost_basis = 0.0
    realized = 0.0
    fees = 0.0

    for state in states.values():
        if state.provenance not in provenances:
            continue

        mark = float(
            mark_prices.get(state.symbol, state.average_cost)
        )

        market_value += state.quantity * mark
        cost_basis += state.quantity * state.average_cost
        realized += state.realized_pnl
        fees += state.fees

    unrealized = market_value - cost_basis

    return AccountingBucket(
        equity=market_value + realized,
        market_value=market_value,
        cost_basis=cost_basis,
        realized_pnl=realized,
        unrealized_pnl=unrealized,
        fees=fees,
    )


def build_ownership_accounting(
    events,
    mark_prices: Mapping[str, float],
) -> OwnershipAccountingSummary:
    states, warnings = replay_owned_positions(events)

    return OwnershipAccountingSummary(
        manual=_bucket(
            states,
            MANUAL_PROVENANCE,
            mark_prices,
        ),
        ats=_bucket(
            states,
            ATS_PROVENANCE,
            mark_prices,
        ),
        unattributed=_bucket(
            states,
            UNATTRIBUTED_PROVENANCE,
            mark_prices,
        ),
        positions=tuple(
            state
            for state in states.values()
            if state.quantity > 0
        ),
        warnings=tuple(warnings),
    )
