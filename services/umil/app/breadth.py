"""Deterministic, cross-sectional market breadth calculations."""

from __future__ import annotations

import statistics
from datetime import datetime, timezone
from typing import Iterable, Mapping

from .contracts import (
    DataQuality,
    DimensionQuality,
    MarketTicker,
    OperationalState,
    UniverseBreadth,
)


def _quality_state(
    *, completeness: float, freshness_ms: int, stale_after_ms: int,
    provider_missing: bool, has_observed: bool,
) -> OperationalState:
    if completeness <= 0:
        return OperationalState.STALE if has_observed else OperationalState.UNAVAILABLE
    if freshness_ms > stale_after_ms:
        return OperationalState.STALE
    if provider_missing or completeness < 0.80:
        return OperationalState.DEGRADED
    if completeness < 0.95:
        return OperationalState.WARMING
    return OperationalState.READY


def calculate_universe_breadth(
    tickers: Iterable[MarketTicker],
    *,
    eligible_instruments: int,
    as_of: datetime | None = None,
    projection_observed_at: datetime | None = None,
    provider_latency_ms: Mapping[str, int] | None = None,
    missing_providers: Iterable[str] = (),
    sequence_gap_recoveries: int = 0,
    stale_after_ms: int = 15_000,
) -> UniverseBreadth:
    as_of = as_of or datetime.now(timezone.utc)
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    if projection_observed_at is not None and projection_observed_at.tzinfo is None:
        raise ValueError("projection_observed_at must be timezone-aware")
    if eligible_instruments < 0:
        raise ValueError("eligible_instruments cannot be negative")
    observed_rows = tuple(tickers)
    if projection_observed_at is None:
        observed_ages_ms = [
            max(0, int((as_of - row.event_time).total_seconds() * 1000))
            for row in observed_rows
        ]
        rows = tuple(
            row for row, age in zip(observed_rows, observed_ages_ms)
            if age <= stale_after_ms
        )
        ages_ms = [
            max(0, int((as_of - row.event_time).total_seconds() * 1000))
            for row in rows
        ]
        freshness_ms = max(ages_ms, default=stale_after_ms + 1)
    else:
        # Binance's all-market mini-ticker feed is a change stream. Once a
        # complete REST snapshot has seeded the book, an uninterrupted fresh
        # stream batch is the projection watermark; quiet symbols do not become
        # stale merely because their 24-hour statistic did not change.
        freshness_ms = max(
            0,
            int((as_of - projection_observed_at).total_seconds() * 1000),
        )
        rows = observed_rows if freshness_ms <= stale_after_ms else ()
    returns = [row.return_24h_pct for row in rows]
    volumes = [row.quote_volume_24h for row in rows]
    advancers = sum(value > 0 for value in returns)
    decliners = sum(value < 0 for value in returns)
    unchanged = len(rows) - advancers - decliners
    completeness = min(1.0, len(rows) / eligible_instruments) if eligible_instruments else 0.0
    missing = tuple(sorted(set(missing_providers)))
    state = _quality_state(
        completeness=completeness,
        freshness_ms=freshness_ms,
        stale_after_ms=stale_after_ms,
        provider_missing=bool(missing),
        has_observed=bool(observed_rows),
    )
    total_volume = sum(volumes)
    weighted_advance = (
        sum(volume for volume, change in zip(volumes, returns) if change > 0) / total_volume
        if total_volume > 0 else None
    )
    hhi = (
        sum((volume / total_volume) ** 2 for volume in volumes)
        if total_volume > 0 else None
    )
    advance_ratio = advancers / len(rows) if rows else None
    median_return = statistics.median(returns) if returns else None
    dispersion = statistics.pstdev(returns) if len(returns) > 1 else (0.0 if returns else None)
    freshness_percentile = max(0.0, min(100.0, 100.0 * (1.0 - freshness_ms / stale_after_ms)))
    flags: list[str] = []
    if completeness < 0.95:
        flags.append("INCOMPLETE_UNIVERSE")
    stale_count = len(observed_rows) - len(rows)
    if stale_count:
        flags.append("STALE_TICKERS")
    if sequence_gap_recoveries:
        flags.append("STREAM_RECOVERY_OCCURRED")
    if missing:
        flags.append("PROVIDER_MISSING")
    synchronization = max(0.0, 1.0 - sequence_gap_recoveries * 0.05)
    quality = DataQuality(
        state=state,
        completeness=completeness,
        synchronization_quality=synchronization,
        freshness_percentile=freshness_percentile,
        provider_latency_ms=dict(provider_latency_ms or {}),
        missing_providers=missing,
        sequence_gap_recoveries=sequence_gap_recoveries,
        stale_dimensions=("breadth",) if state is OperationalState.STALE else (),
        quality_flags=tuple(flags),
        dimensions={
            "breadth": DimensionQuality(
                state=state,
                completeness=completeness,
                freshness_ms=freshness_ms,
                flags=tuple(flags),
            )
        },
    )
    return UniverseBreadth(
        as_of=as_of,
        state=state,
        eligible_instruments=eligible_instruments,
        observed_instruments=len(rows),
        advancers=advancers,
        decliners=decliners,
        unchanged=unchanged,
        advance_ratio=advance_ratio,
        volume_weighted_advance_ratio=weighted_advance,
        median_return_24h_pct=median_return,
        return_dispersion_pct=dispersion,
        volume_concentration_hhi=hhi,
        market_posture=_market_posture(advance_ratio, weighted_advance, median_return),
        quality=quality,
    )


def _market_posture(
    advance_ratio: float | None,
    weighted_advance_ratio: float | None,
    median_return: float | None,
) -> str:
    if advance_ratio is None or weighted_advance_ratio is None or median_return is None:
        return "INSUFFICIENT_EVIDENCE"
    if advance_ratio >= 0.65 and weighted_advance_ratio >= 0.60 and median_return > 0:
        return "BROAD_RISK_ON"
    if advance_ratio <= 0.35 and weighted_advance_ratio <= 0.40 and median_return < 0:
        return "BROAD_RISK_OFF"
    if abs(advance_ratio - weighted_advance_ratio) >= 0.25:
        return "CONCENTRATED_DIVERGENCE"
    return "MIXED"
