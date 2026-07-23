"""Strict transport-neutral contracts for UMIL intelligence."""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable, Mapping

from .authority import AUTHORITY
from .versions import VERSIONS


_INSTRUMENT_ID = re.compile(r"^[a-z0-9_-]+:(spot|perpetual):[A-Z0-9_-]{3,40}$")


class OperationalState(str, Enum):
    WARMING = "WARMING"
    READY = "READY"
    DEGRADED = "DEGRADED"
    STALE = "STALE"
    INVALID = "INVALID"
    UNAVAILABLE = "UNAVAILABLE"


class InstrumentKind(str, Enum):
    SPOT = "spot"
    PERPETUAL = "perpetual"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _finite(name: str, value: float) -> float:
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


def _bounded(name: str, value: float, lower: float, upper: float) -> float:
    value = _finite(name, value)
    if not lower <= value <= upper:
        raise ValueError(f"{name} must be in [{lower}, {upper}]")
    return value


@dataclass(frozen=True, slots=True)
class Instrument:
    instrument_id: str
    venue: str
    venue_symbol: str
    base_asset: str
    quote_asset: str
    kind: InstrumentKind
    status: str
    price_precision: int | None = None
    quantity_precision: int | None = None

    def __post_init__(self) -> None:
        if not _INSTRUMENT_ID.fullmatch(self.instrument_id):
            raise ValueError(f"invalid instrument_id: {self.instrument_id!r}")
        for name in ("venue", "venue_symbol", "base_asset", "quote_asset", "status"):
            if not getattr(self, name):
                raise ValueError(f"{name} cannot be empty")

    def as_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["kind"] = self.kind.value
        return row


@dataclass(frozen=True, slots=True)
class MarketTicker:
    instrument_id: str
    venue_symbol: str
    last_price: float
    open_price_24h: float
    high_price_24h: float
    low_price_24h: float
    base_volume_24h: float
    quote_volume_24h: float
    event_time: datetime
    bid_price: float | None = None
    ask_price: float | None = None
    trade_count_24h: int | None = None

    def __post_init__(self) -> None:
        if not _INSTRUMENT_ID.fullmatch(self.instrument_id):
            raise ValueError("invalid ticker instrument_id")
        for name in (
            "last_price", "open_price_24h", "high_price_24h",
            "low_price_24h", "base_volume_24h", "quote_volume_24h",
        ):
            value = _finite(name, getattr(self, name))
            if value < 0:
                raise ValueError(f"{name} cannot be negative")
        if self.last_price <= 0 or self.open_price_24h <= 0:
            raise ValueError("ticker prices must be positive")
        if self.event_time.tzinfo is None:
            raise ValueError("event_time must be timezone-aware")
        if self.bid_price is not None and _finite("bid_price", self.bid_price) < 0:
            raise ValueError("bid_price cannot be negative")
        if self.ask_price is not None and _finite("ask_price", self.ask_price) < 0:
            raise ValueError("ask_price cannot be negative")
        if self.trade_count_24h is not None and self.trade_count_24h < 0:
            raise ValueError("trade_count_24h cannot be negative")

    @property
    def return_24h_pct(self) -> float:
        return (self.last_price / self.open_price_24h - 1.0) * 100.0

    @property
    def spread_bps(self) -> float | None:
        if not self.bid_price or not self.ask_price:
            return None
        midpoint = (self.bid_price + self.ask_price) / 2.0
        if midpoint <= 0:
            return None
        return (self.ask_price - self.bid_price) / midpoint * 10_000.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "instrument_id": self.instrument_id,
            "venue_symbol": self.venue_symbol,
            "last_price": self.last_price,
            "return_24h_pct": self.return_24h_pct,
            "high_price_24h": self.high_price_24h,
            "low_price_24h": self.low_price_24h,
            "base_volume_24h": self.base_volume_24h,
            "quote_volume_24h": self.quote_volume_24h,
            "spread_bps": self.spread_bps,
            "trade_count_24h": self.trade_count_24h,
            "event_time": self.event_time,
        }


@dataclass(frozen=True, slots=True)
class DimensionQuality:
    state: OperationalState
    completeness: float
    freshness_ms: int
    flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _bounded("dimension completeness", self.completeness, 0.0, 1.0)
        if self.freshness_ms < 0:
            raise ValueError("freshness_ms cannot be negative")

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "completeness": self.completeness,
            "freshness_ms": self.freshness_ms,
            "flags": list(self.flags),
        }


@dataclass(frozen=True, slots=True)
class DataQuality:
    state: OperationalState
    completeness: float
    synchronization_quality: float
    freshness_percentile: float
    provider_latency_ms: Mapping[str, int]
    missing_providers: tuple[str, ...] = ()
    sequence_gap_recoveries: int = 0
    stale_dimensions: tuple[str, ...] = ()
    quality_flags: tuple[str, ...] = ()
    dimensions: Mapping[str, DimensionQuality] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _bounded("completeness", self.completeness, 0.0, 1.0)
        _bounded("synchronization_quality", self.synchronization_quality, 0.0, 1.0)
        _bounded("freshness_percentile", self.freshness_percentile, 0.0, 100.0)
        if self.sequence_gap_recoveries < 0:
            raise ValueError("sequence_gap_recoveries cannot be negative")
        if any(value < 0 for value in self.provider_latency_ms.values()):
            raise ValueError("provider latency cannot be negative")

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "completeness": self.completeness,
            "synchronization_quality": self.synchronization_quality,
            "freshness_percentile": self.freshness_percentile,
            "provider_latency_ms": dict(self.provider_latency_ms),
            "missing_providers": list(self.missing_providers),
            "sequence_gap_recoveries": self.sequence_gap_recoveries,
            "stale_dimensions": list(self.stale_dimensions),
            "quality_flags": list(self.quality_flags),
            "dimensions": {
                key: value.as_dict() for key, value in self.dimensions.items()
            },
        }


@dataclass(frozen=True, slots=True)
class UniverseBreadth:
    as_of: datetime
    state: OperationalState
    eligible_instruments: int
    observed_instruments: int
    advancers: int
    decliners: int
    unchanged: int
    advance_ratio: float | None
    volume_weighted_advance_ratio: float | None
    median_return_24h_pct: float | None
    return_dispersion_pct: float | None
    volume_concentration_hhi: float | None
    market_posture: str
    quality: DataQuality
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")
        counts = (self.eligible_instruments, self.observed_instruments,
                  self.advancers, self.decliners, self.unchanged)
        if any(value < 0 for value in counts):
            raise ValueError("breadth counts cannot be negative")
        if self.advancers + self.decliners + self.unchanged != self.observed_instruments:
            raise ValueError("breadth direction counts do not reconcile")
        for name in ("advance_ratio", "volume_weighted_advance_ratio"):
            value = getattr(self, name)
            if value is not None:
                _bounded(name, value, 0.0, 1.0)
        if self.volume_concentration_hhi is not None:
            _bounded("volume_concentration_hhi", self.volume_concentration_hhi, 0.0, 1.0)

    def as_dict(self) -> dict[str, Any]:
        return {
            "snapshot_schema_version": VERSIONS.snapshot_schema_version,
            "api_schema_version": VERSIONS.api_schema_version,
            "feature_schema_version": VERSIONS.feature_schema_version,
            "quality_schema_version": VERSIONS.quality_schema_version,
            "calculation_version": VERSIONS.calculation_version,
            "as_of": self.as_of,
            "state": self.state.value,
            "eligible_instruments": self.eligible_instruments,
            "observed_instruments": self.observed_instruments,
            "advancers": self.advancers,
            "decliners": self.decliners,
            "unchanged": self.unchanged,
            "advance_ratio": self.advance_ratio,
            "volume_weighted_advance_ratio": self.volume_weighted_advance_ratio,
            "median_return_24h_pct": self.median_return_24h_pct,
            "return_dispersion_pct": self.return_dispersion_pct,
            "volume_concentration_hhi": self.volume_concentration_hhi,
            "market_posture": self.market_posture,
            "quality": self.quality.as_dict(),
            "evidence_ids": list(self.evidence_ids),
            "authority": AUTHORITY.as_dict(),
        }


def require_unique_instruments(instruments: Iterable[Instrument]) -> tuple[Instrument, ...]:
    rows = tuple(instruments)
    identities = [row.instrument_id for row in rows]
    if len(identities) != len(set(identities)):
        raise ValueError("duplicate instrument identity")
    return rows
