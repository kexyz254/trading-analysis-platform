"""Canonical intelligence contract shared by manual and autonomous trading."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Dict, Optional, Tuple


@dataclass(frozen=True)
class TimeframeAnalysis:
    timeframe: str
    bias: str
    confidence: float
    regime: str
    strategy: Optional[str] = None
    evidence: Tuple[str, ...] = ()
    conflicts: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")


@dataclass(frozen=True)
class PairIntelligence:
    symbol: str
    timestamp: datetime

    price: float
    spread_pct: Optional[float]
    liquidity_score: float
    data_quality: float

    regime: str
    direction: str
    confidence: float

    preferred_timeframe: Optional[str]
    preferred_strategy: Optional[str]
    timeframes: Tuple[TimeframeAnalysis, ...]

    entry_zone: Optional[Tuple[float, float]] = None
    invalidation: Optional[float] = None
    stop: Optional[float] = None
    targets: Tuple[float, ...] = ()

    expected_rr: Optional[float] = None
    suggested_risk_pct: Optional[float] = None
    suggested_size: Optional[float] = None

    supporting_evidence: Tuple[str, ...] = ()
    conflicting_evidence: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.price <= 0:
            raise ValueError("price must be positive")

        for name, value in (
            ("confidence", self.confidence),
            ("liquidity_score", self.liquidity_score),
            ("data_quality", self.data_quality),
        ):
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
