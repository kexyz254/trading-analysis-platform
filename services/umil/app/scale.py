"""Supply-aware asset scale with ambiguity explicitly quarantined."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from .observations import AssetScaleObservation


def normalize_market_scale(
    rows: Iterable[dict[str, Any]], *, observed_at: datetime | None = None,
) -> tuple[dict[str, AssetScaleObservation], frozenset[str]]:
    observed_at = observed_at or datetime.now(timezone.utc)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        symbol = str(row.get("symbol", "")).upper()
        provider_id = str(row.get("id", ""))
        if symbol and provider_id:
            grouped.setdefault(symbol, []).append(row)
    output: dict[str, AssetScaleObservation] = {}
    ambiguous: set[str] = set()
    for symbol, candidates in grouped.items():
        ranked = sorted(
            candidates, key=lambda row: float(row.get("market_cap") or 0), reverse=True
        )
        if len(ranked) > 1:
            ambiguous.add(symbol)
        row = ranked[0]
        output[symbol] = AssetScaleObservation(
            asset_id=str(row["id"]), observed_at=observed_at,
            market_cap_usd=_optional_float(row.get("market_cap")),
            fully_diluted_value_usd=_optional_float(row.get("fully_diluted_valuation")),
            circulating_supply=_optional_float(row.get("circulating_supply")),
            rank=_optional_int(row.get("market_cap_rank")), sector=None,
        )
    return output, frozenset(ambiguous)


def _optional_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
