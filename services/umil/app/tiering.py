"""Deterministic, bounded data-coverage tiers for a large universe."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .contracts import MarketTicker


@dataclass(frozen=True, slots=True)
class TierAssignment:
    instrument_id: str
    tier: str
    reason: str
    priority: float


def assign_tiers(
    tickers: Iterable[MarketTicker], *, active_limit: int = 100, focus_limit: int = 20,
    viewed_instruments: Iterable[str] = (), anomaly_instruments: Iterable[str] = (),
) -> tuple[TierAssignment, ...]:
    rows = tuple(tickers)
    viewed = set(viewed_instruments)
    anomalous = set(anomaly_instruments)
    scored = sorted(
        rows,
        key=lambda row: (row.quote_volume_24h, row.trade_count_24h or 0),
        reverse=True,
    )
    active_ids = {row.instrument_id for row in scored[:active_limit]}
    focus_ids = {row.instrument_id for row in scored[:focus_limit]} | viewed | anomalous
    assignments = []
    for index, row in enumerate(scored):
        if row.instrument_id in focus_ids:
            tier, reason = "FOCUS", "viewed_or_anomalous" if row.instrument_id in viewed | anomalous else "top_liquidity"
        elif row.instrument_id in active_ids:
            tier, reason = "ACTIVE", "liquidity_rank"
        else:
            tier, reason = "UNIVERSE", "lightweight_coverage"
        assignments.append(TierAssignment(
            row.instrument_id, tier, reason,
            max(0.0, 100.0 - index / max(1, len(scored) - 1) * 100.0),
        ))
    return tuple(assignments)
