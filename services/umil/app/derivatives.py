"""Derivatives context separated from spot demand and direction claims."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .observations import DerivativesObservation


@dataclass(frozen=True, slots=True)
class DerivativesMetrics:
    as_of: str
    funding_rate_pct: float
    basis_bps: float
    open_interest: float
    open_interest_change_pct: float | None
    crowding_score: float
    crowding_state: str
    risk_flags: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        row = asdict(self)
        row["risk_flags"] = list(self.risk_flags)
        return row


def calculate_derivatives_metrics(row: DerivativesObservation) -> DerivativesMetrics:
    basis = (row.mark_price / row.index_price - 1.0) * 10_000.0
    oi_change = None
    if row.previous_open_interest is not None and row.previous_open_interest > 0:
        oi_change = (row.open_interest / row.previous_open_interest - 1.0) * 100.0
    funding_pct = row.funding_rate * 100.0
    funding_pressure = min(50.0, abs(funding_pct) / 0.1 * 35.0)
    basis_pressure = min(30.0, abs(basis) / 100.0 * 30.0)
    oi_pressure = min(20.0, abs(oi_change or 0.0) / 10.0 * 20.0)
    crowding = min(100.0, funding_pressure + basis_pressure + oi_pressure)
    flags: list[str] = []
    if abs(funding_pct) >= 0.1:
        flags.append("EXTREME_FUNDING")
    if abs(basis) >= 100:
        flags.append("EXTREME_BASIS")
    if oi_change is not None and abs(oi_change) >= 10:
        flags.append("RAPID_OPEN_INTEREST_CHANGE")
    if crowding >= 70:
        state = "EXTREME_CROWDING"
    elif crowding >= 40:
        state = "ELEVATED_CROWDING"
    else:
        state = "NORMAL"
    return DerivativesMetrics(
        as_of=row.observed_at.isoformat(), funding_rate_pct=funding_pct,
        basis_bps=basis, open_interest=row.open_interest,
        open_interest_change_pct=oi_change, crowding_score=crowding,
        crowding_state=state, risk_flags=tuple(flags),
    )
