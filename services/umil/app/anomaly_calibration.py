"""Deterministic anomaly calibration for research ranking.

The lightweight scanner deliberately prioritizes recall and may saturate at 100.
This module converts its raw signal into a differentiated, quality-aware research
ranking without inventing unavailable market dimensions or trade probabilities.
"""

from __future__ import annotations

import math
from typing import Any, Mapping


_DNA_KEYS = ("TRN", "MOM", "PRS", "STR", "VOL", "LIQ", "ACT", "RSK")


def _number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _clamp(value: float, lower: float = 0.0, upper: float = 100.0) -> float:
    return max(lower, min(upper, value))


def _return_extremity(percentile: Any) -> float:
    """Distance from the cross-sectional median, normalized to [0, 100]."""
    return _clamp(abs(_number(percentile, 50.0) - 50.0) * 2.0)


def _range_component(range_pct: Any) -> float:
    """Smoothly scale observed intraday range without a hard early ceiling."""
    value = max(0.0, _number(range_pct))
    # 2% -> 15, 10% -> 50, 25% -> 76, 50% -> 89, 100% -> 96.
    return _clamp(100.0 * (1.0 - math.exp(-value / 14.5)))


def _range_location_component(position: Any) -> float:
    """Reward observations near either edge of their reported daily range."""
    value = _clamp(_number(position, 0.5), 0.0, 1.0)
    return _clamp(abs(value - 0.5) * 200.0)


def _dna_divergence(dna: Any) -> float | None:
    if not isinstance(dna, Mapping):
        return None
    values = [
        abs(_clamp(_number(dna.get(key), 50.0)) - 50.0) * 2.0
        for key in _DNA_KEYS
        if key in dna
    ]
    return sum(values) / len(values) if values else None


def _confidence(
    universe_state: Mapping[str, Any],
    *,
    has_dna: bool,
    has_evidence: bool,
) -> tuple[float, str, str]:
    available = universe_state.get("available_dimensions")
    missing = universe_state.get("missing_dimensions")
    available_count = len(available) if isinstance(available, list) else 0
    missing_count = len(missing) if isinstance(missing, list) else 0
    total = available_count + missing_count
    completeness = available_count / total if total else 0.0

    score = 35.0 + (35.0 * completeness)
    if has_dna:
        score += 15.0
    if has_evidence:
        score += 10.0

    flags = {
        str(value).upper()
        for value in universe_state.get("quality_flags", [])
        if isinstance(value, str)
    }
    state = str(universe_state.get("state", "UNKNOWN")).upper()
    if "SPREAD_UNAVAILABLE" not in flags:
        score += 5.0
    if state == "STALE" or "STALE_TICKER" in flags:
        score *= 0.75
    elif state not in {"READY", "LIVE"}:
        score *= 0.60

    score = round(_clamp(score), 2)
    tier = "HIGH" if score >= 80.0 else "MEDIUM" if score >= 60.0 else "LOW"
    if has_dna and has_evidence and state in {"READY", "LIVE"}:
        evidence_tier = "EVIDENCE_BACKED"
    elif has_dna or has_evidence:
        evidence_tier = "PARTIAL_EVIDENCE"
    else:
        evidence_tier = "UNIVERSE_ONLY"
    return score, tier, evidence_tier


def calibrate_anomaly(
    universe_state: Mapping[str, Any],
    detailed: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a bounded, differentiated and quality-aware anomaly ranking.

    The returned ``anomaly_score`` is a research-ranking score, not a return
    forecast or execution probability. ``raw_anomaly_score`` preserves the
    upstream scanner maximum for auditability.
    """
    detailed = detailed if isinstance(detailed, Mapping) else None
    quick_raw = _clamp(_number(universe_state.get("anomaly_score")))
    detailed_raw = _clamp(_number(detailed.get("anomaly_score"))) if detailed else 0.0
    raw_score = max(quick_raw, detailed_raw)

    return_extremity = _return_extremity(universe_state.get("return_percentile"))
    volume = _clamp(_number(universe_state.get("volume_percentile"), 50.0))
    range_size = _range_component(universe_state.get("intraday_range_pct"))
    range_location = _range_location_component(universe_state.get("range_position"))
    dna = detailed.get("dna") if detailed else None
    dna_divergence = _dna_divergence(dna)

    if dna_divergence is None:
        base_signal = (
            0.30 * raw_score
            + 0.30 * return_extremity
            + 0.22 * volume
            + 0.13 * range_size
            + 0.05 * range_location
        )
    else:
        base_signal = (
            0.22 * raw_score
            + 0.25 * return_extremity
            + 0.18 * volume
            + 0.12 * range_size
            + 0.05 * range_location
            + 0.18 * dna_divergence
        )

    evidence_id = detailed.get("evidence_record_id") if detailed else None
    confidence_score, confidence_tier, evidence_tier = _confidence(
        universe_state,
        has_dna=dna_divergence is not None,
        has_evidence=isinstance(evidence_id, str) and bool(evidence_id.strip()),
    )
    quality_multiplier = 0.65 + (0.35 * confidence_score / 100.0)
    calibrated = round(_clamp(base_signal * quality_multiplier, upper=99.9), 2)

    return {
        "anomaly_score": calibrated,
        "raw_anomaly_score": round(raw_score, 2),
        "confidence_score": confidence_score,
        "confidence_tier": confidence_tier,
        "evidence_tier": evidence_tier,
        "usage": "RESEARCH_ALERT",
        "trade_eligible": False,
        "score_components": {
            "raw_signal": round(raw_score, 2),
            "return_extremity": round(return_extremity, 2),
            "volume_percentile": round(volume, 2),
            "intraday_range": round(range_size, 2),
            "range_location": round(range_location, 2),
            "dna_divergence": round(dna_divergence, 2) if dna_divergence is not None else None,
            "quality_multiplier": round(quality_multiplier, 4),
        },
    }
