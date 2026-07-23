"""Versioned Market DNA synthesis with inspectable component lineage."""

from __future__ import annotations

import math
import statistics
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from typing import Mapping, Sequence

from .authority import AUTHORITY
from .derivatives import DerivativesMetrics
from .features import TimeframeFeatures
from .microstructure import BookMetrics, PressureAssessment
from .normalization import blended_percentile
from .versions import VERSIONS


DNA_KEYS = ("TRN", "MOM", "PRS", "STR", "VOL", "LIQ", "ACT", "RSK")


def _clamp(value: float) -> float:
    return round(max(0.0, min(100.0, float(value))), 2)


def _mean(values: Sequence[float], default: float = 50.0) -> float:
    return statistics.fmean(values) if values else default


@dataclass(frozen=True, slots=True)
class DNAComponent:
    score: float
    state: str
    confidence: float
    inputs: tuple[str, ...]
    evidence_ids: tuple[str, ...] = ()
    raw_score: float | None = None
    time_series_percentile: float | None = None
    cross_sectional_percentile: float | None = None
    normalization_state: str = "UNCALIBRATED"
    history_count: int = 0
    peer_count: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "score": _clamp(self.score), "state": self.state,
            "confidence": _clamp(self.confidence), "inputs": list(self.inputs),
            "evidence_ids": list(self.evidence_ids),
            "raw_score": _clamp(self.raw_score) if self.raw_score is not None else None,
            "time_series_percentile": self.time_series_percentile,
            "cross_sectional_percentile": self.cross_sectional_percentile,
            "normalization_state": self.normalization_state,
            "history_count": self.history_count,
            "peer_count": self.peer_count,
        }


@dataclass(frozen=True, slots=True)
class MarketDNASnapshot:
    snapshot_id: str
    instrument_id: str
    as_of: datetime
    state: str
    clarity: float
    confidence: float
    components: Mapping[str, DNAComponent]
    quality_flags: tuple[str, ...]
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.as_of.tzinfo is None:
            raise ValueError("Market DNA as_of must be timezone-aware")
        if set(self.components) != set(DNA_KEYS):
            raise ValueError("Market DNA requires exactly eight dimensions")

    def as_dict(self) -> dict[str, object]:
        return {
            "snapshot_id": self.snapshot_id,
            "instrument_id": self.instrument_id,
            "as_of": self.as_of,
            "state": self.state,
            "clarity": _clamp(self.clarity),
            "confidence": _clamp(self.confidence),
            "directional_probability": None,
            "dna": {key: _clamp(value.score) for key, value in self.components.items()},
            "dimensions": {key: value.as_dict() for key, value in self.components.items()},
            "quality_flags": list(self.quality_flags),
            "evidence_ids": list(self.evidence_ids),
            "versions": VERSIONS.as_dict(),
            "authority": AUTHORITY.as_dict(),
        }


def _trend_component(rows: Sequence[TimeframeFeatures]) -> DNAComponent:
    slopes = [row.ema_slope_pct for row in rows if row.ema_slope_pct is not None]
    returns = [row.return_6_pct for row in rows if row.return_6_pct is not None]
    raw = _mean([50.0 + math.tanh(value / 2.0) * 40.0 for value in slopes])
    if returns:
        raw = raw * 0.7 + _mean([50.0 + math.tanh(value / 5.0) * 35.0 for value in returns]) * 0.3
    agreement = 100.0
    if len(slopes) > 1:
        signs = [value >= 0 for value in slopes]
        agreement = max(sum(signs), len(signs) - sum(signs)) / len(signs) * 100.0
    state = "BULLISH" if raw >= 60 else "BEARISH" if raw <= 40 else "NEUTRAL"
    return DNAComponent(raw, state, agreement, ("ema_slope_pct", "return_6_pct"))


def _momentum_component(rows: Sequence[TimeframeFeatures]) -> DNAComponent:
    rsi_values = [row.rsi_14 for row in rows if row.rsi_14 is not None]
    accelerations = [row.momentum_acceleration for row in rows if row.momentum_acceleration is not None]
    raw = _mean(rsi_values)
    if accelerations:
        raw += math.tanh(_mean(accelerations) / 10.0) * 12.0
    state = "ACCELERATING" if raw >= 60 else "WEAKENING" if raw <= 40 else "BALANCED"
    return DNAComponent(raw, state, min(100.0, len(rsi_values) / 3.0 * 100.0), ("rsi_14", "momentum_acceleration"))


def _structure_component(rows: Sequence[TimeframeFeatures]) -> DNAComponent:
    positions = [row.range_position for row in rows if row.range_position is not None]
    breakouts = sum(row.structure_state == "BREAKOUT_UP" for row in rows)
    breakdowns = sum(row.structure_state == "BREAKOUT_DOWN" for row in rows)
    raw = _mean([value * 100.0 for value in positions]) + (breakouts - breakdowns) * 8.0
    dominant = max((row.structure_state for row in rows), key=lambda state: sum(r.structure_state == state for r in rows), default="UNKNOWN")
    return DNAComponent(raw, dominant, min(100.0, len(positions) / 3.0 * 100.0), ("range_position", "structure_state"))


def build_market_dna(
    *, snapshot_id: str, instrument_id: str, as_of: datetime,
    timeframes: Mapping[str, TimeframeFeatures],
    pressure: PressureAssessment | None = None,
    book: BookMetrics | None = None,
    derivatives: DerivativesMetrics | None = None,
    evidence_ids: Sequence[str] = (),
) -> MarketDNASnapshot:
    rows = tuple(timeframes.values())
    flags: list[str] = []
    trend = _trend_component(rows)
    momentum = _momentum_component(rows)
    structure = _structure_component(rows)
    vol_values = [row.realized_volatility_pct for row in rows if row.realized_volatility_pct is not None]
    vol_score = _clamp(50.0 + math.tanh(_mean(vol_values, 0.0) / 8.0) * 45.0)
    volatility = DNAComponent(vol_score, "EXPANDED" if vol_score >= 65 else "COMPRESSED" if vol_score <= 35 else "NORMAL", min(100.0, len(vol_values) / 3.0 * 100.0), ("realized_volatility_pct", "atr_pct"))
    activity_values = [row.relative_volume_z for row in rows if row.relative_volume_z is not None]
    activity_score = 50.0 + math.tanh(_mean(activity_values, 0.0) / 2.0) * 45.0
    activity = DNAComponent(activity_score, "ELEVATED" if activity_score >= 65 else "QUIET" if activity_score <= 35 else "NORMAL", min(100.0, len(activity_values) / 3.0 * 100.0), ("relative_volume_z",))
    if pressure is None:
        flags.append("PRESSURE_WARMING")
        pressure_component = DNAComponent(50.0, "WARMING", 0.0, ())
    else:
        pressure_component = DNAComponent(pressure.score, pressure.label, pressure.confidence, pressure.evidence)
    if book is None:
        flags.append("LIQUIDITY_DEPTH_WARMING")
        liquidity = DNAComponent(50.0, "WARMING", 0.0, ())
    else:
        liquidity_score = book.resilience_score * 0.7 + max(0.0, 100.0 - book.spread_bps * 5.0) * 0.3
        liquidity = DNAComponent(liquidity_score, "DEEP" if liquidity_score >= 75 else "THIN" if liquidity_score <= 40 else "NORMAL", 90.0, ("spread_bps", "slippage_bps", "resilience_score"))
    completeness = _mean([row.completeness for row in rows], 0.0)
    risk_score = (100.0 - completeness * 100.0) * 0.45 + vol_score * 0.20 + (100.0 - liquidity.score) * 0.20
    if derivatives is not None:
        risk_score += derivatives.crowding_score * 0.15
    else:
        flags.append("DERIVATIVES_UNAVAILABLE")
    risk = DNAComponent(risk_score, "HIGH" if risk_score >= 65 else "LOW" if risk_score <= 35 else "MODERATE", completeness * 100.0, ("evidence_completeness", "volatility", "liquidity", "derivatives_crowding"))
    components = {
        "TRN": trend, "MOM": momentum, "PRS": pressure_component,
        "STR": structure, "VOL": volatility, "LIQ": liquidity,
        "ACT": activity, "RSK": risk,
    }
    directional = [trend.score - 50.0, momentum.score - 50.0, pressure_component.score - 50.0, structure.score - 50.0]
    clarity = abs(statistics.fmean(directional)) * 2.0
    if len(directional) > 1:
        clarity *= max(0.0, 1.0 - statistics.pstdev(directional) / 50.0)
    confidence = _mean([component.confidence for component in components.values()])
    confidence = min(confidence, completeness * 100.0)
    state = "READY" if confidence >= 75 and not flags[:2] else "DEGRADED" if confidence >= 40 else "WARMING"
    return MarketDNASnapshot(
        snapshot_id=snapshot_id, instrument_id=instrument_id, as_of=as_of,
        state=state, clarity=clarity, confidence=confidence,
        components=components, quality_flags=tuple(flags),
        evidence_ids=tuple(evidence_ids),
    )


def normalize_market_dna(
    snapshot: MarketDNASnapshot,
    *,
    histories: Mapping[str, Sequence[float]],
    peers: Mapping[str, Sequence[float]],
) -> MarketDNASnapshot:
    """Calibrate each dimension only from evidence available at snapshot time."""
    components: dict[str, DNAComponent] = {}
    flags = list(snapshot.quality_flags)
    calibrated = 0
    for key, component in snapshot.components.items():
        history = tuple(histories.get(key, ()))
        cross_section = tuple(peers.get(key, ()))
        signal = blended_percentile(
            component.score, history=history, peers=cross_section,
            higher_is_stronger=True,
        )
        sufficient = len(history) >= 20 or len(cross_section) >= 10
        components[key] = replace(
            component,
            score=signal.score if sufficient else component.score,
            raw_score=component.score,
            time_series_percentile=signal.time_series_percentile,
            cross_sectional_percentile=signal.cross_sectional_percentile,
            normalization_state="CALIBRATED" if sufficient else "WARMING",
            history_count=signal.history_count,
            peer_count=signal.peer_count,
        )
        calibrated += int(sufficient)
    if calibrated < len(DNA_KEYS):
        flags.append("NORMALIZATION_WARMING")
    confidence_ceiling = snapshot.confidence * (0.75 + 0.25 * calibrated / len(DNA_KEYS))
    return replace(
        snapshot,
        components=components,
        confidence=min(snapshot.confidence, confidence_ceiling),
        quality_flags=tuple(dict.fromkeys(flags)),
    )
