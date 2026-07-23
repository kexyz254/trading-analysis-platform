"""Deterministic, read-only synthesis for the ATS research dashboard.

The interpreter combines already-produced evidence without training models,
creating decisions, changing tournament state, or opening exposure.  Its
alignment score measures agreement between independent evidence paths; it is
never a probability of profit or a substitute for the economic gate.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional


INTERPRETER_VERSION = "market-interpreter-v1.0.0"
_TIMEFRAME_MS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
}


def _number(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _epoch_ms(value: Any) -> Optional[int]:
    number = _number(value)
    if number is not None:
        return int(number if number > 10_000_000_000 else number * 1000)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def _sign(direction: Any) -> int:
    value = str(direction or "").upper().replace(" ", "_")
    if value in {"UP", "BUY", "BULLISH", "TRENDING_UP"}:
        return 1
    if value in {"DOWN", "SELL", "BEARISH", "TRENDING_DOWN"}:
        return -1
    return 0


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _append_once(items: List[str], value: str) -> None:
    if value and value not in items:
        items.append(value)


def _forecast_evidence(forecast: Dict[str, Any]) -> Dict[str, Any]:
    horizons = list(forecast.get("horizons") or [])
    validated: List[Dict[str, Any]] = []
    all_signs: List[int] = []
    for item in horizons:
        sign = _sign(item.get("direction"))
        if sign:
            all_signs.append(sign)
        hit_rate = _number(item.get("hit_rate"))
        up_share = _number(item.get("up_share_holdout"))
        probability_up = _number(item.get("probability_up"))
        if None in (hit_rate, up_share, probability_up) or sign == 0:
            continue
        baseline = max(up_share, 1.0 - up_share)
        edge = hit_rate - baseline
        if edge <= 0:
            continue
        directional_strength = abs(probability_up - 0.5) * 2.0
        validation_strength = _clamp(edge / 0.10)
        validated.append(
            {
                "step": int(item.get("step") or 0),
                "direction": "UP" if sign > 0 else "DOWN",
                "score": sign * directional_strength * validation_strength,
                "hit_rate": hit_rate,
                "baseline": baseline,
            }
        )
    if not all_signs:
        direction = "NO_EVIDENCE"
    elif all(sign > 0 for sign in all_signs):
        direction = "UP"
    elif all(sign < 0 for sign in all_signs):
        direction = "DOWN"
    else:
        direction = "MIXED"
    score = (
        sum(item["score"] for item in validated) / len(validated)
        if validated else None
    )
    return {
        "state": "VALIDATED_EDGE" if validated else (
            "READY_LOW_EDGE" if horizons else "NO_EVIDENCE"
        ),
        "direction": direction,
        "score": score,
        "validated_horizons": len(validated),
        "total_horizons": len(horizons),
        "details": validated,
        "as_of_ms": _epoch_ms(forecast.get("as_of")),
        "model_fingerprint": forecast.get("model_fingerprint"),
    }


def _pattern_evidence(patterns: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    cleaned = []
    for item in patterns or []:
        bias = str(item.get("bias") or "neutral").lower()
        confidence = _clamp(_number(item.get("confidence")) or 0.0)
        cleaned.append(
            {
                "name": str(item.get("name") or "unknown"),
                "bias": bias if bias in {"bullish", "bearish", "neutral"} else "neutral",
                "geometry_confidence": confidence,
            }
        )
    cleaned.sort(key=lambda item: item["geometry_confidence"], reverse=True)
    denominator = sum(item["geometry_confidence"] for item in cleaned)
    score = None
    if denominator > 0:
        score = sum(
            _sign(item["bias"]) * item["geometry_confidence"]
            for item in cleaned
        ) / denominator
    return {
        "state": "PATTERNS_PRESENT" if cleaned else "NO_RIGHT_EDGE_PATTERN",
        "score": score,
        "bullish": sum(item["bias"] == "bullish" for item in cleaned),
        "bearish": sum(item["bias"] == "bearish" for item in cleaned),
        "neutral": sum(item["bias"] == "neutral" for item in cleaned),
        "leading_patterns": cleaned[:3],
        "geometry_is_not_probability": True,
    }


def _economic_context(
    timeframe: str,
    open_calls: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    if timeframe not in {"5m", "15m"}:
        return {
            "state": "NO_MATCHING_ECONOMIC_HORIZON",
            "action": None,
            "direction": None,
            "primary_eligible": False,
        }
    call = next(
        (
            item for item in (open_calls or [])
            if str(item.get("horizon_label")) == timeframe
        ),
        None,
    )
    if call is None:
        return {
            "state": "WAITING_FOR_BOUNDARY_CALL",
            "action": None,
            "direction": None,
            "primary_eligible": False,
        }
    action = str(call.get("economic_action") or "NO_TRADE").upper()
    primary = bool(call.get("primary_benchmark_eligible"))
    state = (
        "TRADE_ELIGIBLE" if action == "TRADE" and primary
        else "ABSTAIN" if action == "NO_TRADE"
        else "EVIDENCE_EXCLUDED" if not primary
        else "OBSERVING"
    )
    return {
        "state": state,
        "action": action,
        "direction": call.get("direction"),
        "primary_eligible": primary,
        "timing_class": call.get("timing_class"),
        "decision_code": call.get("economic_decision_code"),
        "cycle_start_ms": call.get("cycle_start_ms"),
        "due_at_ms": call.get("due_at"),
    }


def build_market_interpretation(
    *,
    symbol: str,
    timeframe: str,
    engine_timeframe: str,
    legacy_prediction: Optional[Dict[str, Any]] = None,
    forecast: Optional[Dict[str, Any]] = None,
    patterns: Optional[Iterable[Dict[str, Any]]] = None,
    tournament: Optional[Dict[str, Any]] = None,
    open_calls: Optional[Iterable[Dict[str, Any]]] = None,
    source_errors: Optional[Iterable[str]] = None,
    now_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """Build one source-labeled interpretation with fail-closed authority."""
    symbol = str(symbol).upper()
    timeframe = str(timeframe)
    now_ms = int(now_ms or datetime.now(timezone.utc).timestamp() * 1000)
    legacy_prediction = dict(legacy_prediction or {})
    forecast = dict(forecast or {})
    tournament = dict(tournament or {})
    warnings: List[str] = []
    conflicts: List[str] = []
    for error in source_errors or []:
        _append_once(warnings, str(error))

    market_state = (
        tournament.get("market_state", {}).get("horizons", {}).get(timeframe)
        or {}
    )
    market_data_state = str(market_state.get("data_state") or "NO_EVIDENCE")
    regime = str(market_state.get("regime") or "UNCLASSIFIED").upper()
    regime_confidence = _clamp(_number(market_state.get("regime_confidence")) or 0.0)
    market_score = _sign(regime) * regime_confidence if market_data_state == "READY" else None
    market_as_of_ms = _epoch_ms(market_state.get("latest_close_time_ms"))

    if timeframe not in {"5m", "15m"}:
        _append_once(warnings, "MARKET_STATE_NOT_AVAILABLE_FOR_SELECTED_TIMEFRAME")
    elif market_data_state != "READY":
        _append_once(warnings, "MARKET_STATE_NOT_READY")
    interval_ms = _TIMEFRAME_MS.get(timeframe)
    if market_as_of_ms and interval_ms and now_ms - market_as_of_ms > interval_ms * 3:
        _append_once(warnings, "MARKET_STATE_STALE")

    forecast_view = _forecast_evidence(forecast)
    if forecast_view["state"] == "NO_EVIDENCE":
        _append_once(warnings, "FORECAST_ML_NO_EVIDENCE")
    elif forecast_view["state"] == "READY_LOW_EDGE":
        _append_once(warnings, "FORECAST_ML_NO_VALIDATED_EDGE")
    forecast_as_of_ms = forecast_view["as_of_ms"]
    if forecast_as_of_ms and interval_ms and now_ms - forecast_as_of_ms > interval_ms * 3:
        _append_once(warnings, "FORECAST_ML_STALE")

    pattern_view = _pattern_evidence(patterns or [])
    legacy_signal = str(legacy_prediction.get("signal") or "HOLD").upper()
    legacy_matches = str(engine_timeframe) == timeframe
    legacy_score = None
    if legacy_signal in {"BUY", "SELL"} and legacy_matches:
        confidence = _number(legacy_prediction.get("confidence"))
        legacy_score = _sign(legacy_signal) * _clamp(
            confidence if confidence is not None else 0.5
        )
    elif legacy_signal in {"BUY", "SELL"} and not legacy_matches:
        _append_once(warnings, "LEGACY_SIGNAL_TIMEFRAME_MISMATCH")

    tournament_ml = (
        tournament.get("learning", {}).get("tournament_ml", {})
        .get("horizons", {}).get(timeframe, {})
    )
    tournament_ml_state = str(tournament_ml.get("state") or "NO_EVIDENCE")
    if tournament_ml_state not in {"READY", "ACTIVE"}:
        _append_once(warnings, f"TOURNAMENT_ML_{tournament_ml_state}")
    horizon_health = tournament.get("horizons", {}).get(timeframe, {}).get("health", {})
    if horizon_health.get("state") == "DEGRADED":
        _append_once(warnings, "TOURNAMENT_INTEGRITY_DEGRADED")

    evidence = []
    for source, score, weight in (
        ("MARKET_REGIME", market_score, 0.40),
        ("FORECAST_ML", forecast_view["score"], 0.35),
        ("CHART_GEOMETRY", pattern_view["score"], 0.15),
        ("LEGACY_STRATEGY", legacy_score, 0.10),
    ):
        if score is not None:
            evidence.append({"source": source, "score": float(score), "weight": weight})

    weight_total = sum(item["weight"] for item in evidence)
    alignment = (
        sum(item["score"] * item["weight"] for item in evidence) / weight_total
        if weight_total else None
    )
    strong_positive = any(item["score"] >= 0.20 for item in evidence)
    strong_negative = any(item["score"] <= -0.20 for item in evidence)
    if strong_positive and strong_negative:
        outlook = "MIXED"
        _append_once(conflicts, "DIRECTIONAL_SOURCES_DISAGREE")
    elif alignment is None:
        outlook = "INSUFFICIENT_EVIDENCE"
    elif alignment >= 0.25:
        outlook = "BULLISH"
    elif alignment <= -0.25:
        outlook = "BEARISH"
    elif abs(alignment) < 0.10:
        outlook = "NEUTRAL"
    else:
        outlook = "MIXED"

    regime_sign = _sign(regime) if market_score is not None else 0
    forecast_sign = _sign(forecast_view["direction"])
    if regime_sign and forecast_sign and regime_sign != forecast_sign:
        _append_once(conflicts, "REGIME_FORECAST_DIVERGENCE")
    pattern_sign = _sign("BULLISH" if (pattern_view["score"] or 0) > 0 else "BEARISH") if pattern_view["score"] else 0
    if pattern_sign and forecast_sign and pattern_sign != forecast_sign:
        _append_once(conflicts, "PATTERN_FORECAST_DIVERGENCE")

    economics = _economic_context(timeframe, open_calls or [])
    outlook_sign = _sign(outlook)
    call_sign = _sign(economics.get("direction"))
    if outlook_sign and call_sign and outlook_sign != call_sign:
        _append_once(conflicts, "PRICE_CALL_OUTLOOK_DIVERGENCE")

    coverage = len(evidence)
    if (
        alignment is not None and abs(alignment) >= 0.60
        and coverage >= 3 and not conflicts
    ):
        conviction = "HIGH"
    elif alignment is not None and abs(alignment) >= 0.30 and coverage >= 2:
        conviction = "MODERATE"
    else:
        conviction = "LOW"

    if "TOURNAMENT_INTEGRITY_DEGRADED" in warnings:
        state = "DEGRADED"
    elif not evidence:
        state = "NO_EVIDENCE"
    elif source_errors or any(item.endswith("_STALE") for item in warnings):
        state = "PARTIAL"
    elif coverage >= 2:
        state = "READY"
    else:
        state = "PARTIAL"

    if outlook == "INSUFFICIENT_EVIDENCE":
        summary = f"Insufficient closed-candle evidence to interpret {symbol} {timeframe}."
    else:
        summary = f"Closed-candle evidence is {outlook.lower()} for {symbol} on {timeframe}."
    if economics["state"] == "ABSTAIN":
        summary += " The economic gate remains NO TRADE."
    elif economics["state"] == "TRADE_ELIGIBLE":
        summary += " The separate economic gate marks the current paper cycle eligible."
    summary += " Alignment is explanatory, not a profit probability."

    legacy_state = (
        "CONTEXT_ONLY" if legacy_signal in {"BUY", "SELL"} and not legacy_matches
        else "READY" if legacy_signal in {"BUY", "SELL"}
        else "HOLD" if legacy_prediction
        else "NO_EVIDENCE"
    )
    return {
        "version": INTERPRETER_VERSION,
        "symbol": symbol,
        "timeframe": timeframe,
        "as_of_ms": now_ms,
        "state": state,
        "outlook": outlook,
        "conviction": conviction,
        "alignment_score": round(alignment * 100, 1) if alignment is not None else None,
        "alignment_score_is_probability": False,
        "summary": summary,
        "market_context": {
            "state": market_data_state,
            "regime": regime,
            "regime_confidence": regime_confidence if market_state else None,
            "latest_closed_candle_ms": market_as_of_ms,
            "source": "POINT_IN_TIME_MARKET_STATE",
        },
        "chart_context": pattern_view,
        "ai_paths": {
            "legacy_strategy": {
                "state": legacy_state,
                "direction": legacy_signal,
                "engine_timeframe": engine_timeframe,
                "selected_timeframe": timeframe,
                "as_of_ms": _epoch_ms(legacy_prediction.get("timestamp")),
                "execution_authority": True,
            },
            "forecast_ml": {
                **forecast_view,
                "execution_authority": False,
                "promotion_authority": False,
            },
            "tournament_ml": {
                "state": tournament_ml_state,
                "direction": "NOT_EXPOSED",
                "decision_state": tournament_ml.get("decision_state"),
                "training_samples": int(tournament_ml.get("training_samples") or 0),
                "model_fingerprint": tournament_ml.get("model_fingerprint"),
                "last_inference_at_ms": tournament_ml.get("last_inference_at_ms"),
                "execution_authority": False,
                "promotion_authority": True,
            },
        },
        "economics": {
            **economics,
            "separate_from_directional_outlook": True,
            "simulated_execution_only": True,
        },
        "evidence": evidence,
        "conflicts": conflicts,
        "warnings": warnings,
        "authority": {
            "read_only": True,
            "can_train_models": False,
            "can_create_decisions": False,
            "can_open_exposure": False,
            "can_change_risk": False,
            "can_promote_candidates": False,
        },
        "confidentiality": {
            "raw_feature_vectors_exposed": False,
            "model_weights_exposed": False,
            "private_thresholds_exposed": False,
            "aggregated_evidence_only": True,
        },
    }
