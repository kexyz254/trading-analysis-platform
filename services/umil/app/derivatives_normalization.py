"""Normalize public futures context without merging it into spot facts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .observations import DerivativesObservation


def normalize_binance_futures_context(
    symbol: str, payload: dict[str, Any], *, previous_open_interest: float | None = None,
) -> DerivativesObservation:
    premium = payload["premium"]
    interest = payload["open_interest"]
    observed_ms = premium.get("time") or interest.get("time")
    observed_at = (
        datetime.fromtimestamp(float(observed_ms) / 1000.0, tz=timezone.utc)
        if observed_ms else datetime.now(timezone.utc)
    )
    return DerivativesObservation(
        instrument_id=f"binance:perpetual:{symbol.upper()}",
        observed_at=observed_at,
        mark_price=float(premium["markPrice"]),
        index_price=float(premium["indexPrice"]),
        funding_rate=float(premium["lastFundingRate"]),
        open_interest=float(interest["openInterest"]),
        previous_open_interest=previous_open_interest,
    )
