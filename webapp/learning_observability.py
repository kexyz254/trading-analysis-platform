"""Read-only learning-path telemetry for the professional ATS dashboard.

This module never trains, promotes, submits, or settles anything.  It reports
independent evidence for the legacy execution model, dashboard forecast models,
and tournament model so a generic process-health light cannot impersonate
proof of learning activity.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

OBSERVABILITY_VERSION = "learning-observability-v1.3.1"


def _epoch_ms(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return int(number if number > 10_000_000_000 else number * 1000)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def _legacy_health(
    db_path: str,
    symbol: str,
    *,
    timeframe: str,
    retrain_interval_hours: float,
) -> Dict[str, Any]:
    from webapp import data_access as da
    from trading_assistant.persistence.journal import Journal

    warnings = []
    journal = Journal(db_path, initialize=False)
    decisions = list(
        journal.recent_decisions(symbol=symbol, limit=1, exclude_paper=True)
    )
    decision = decisions[0] if decisions else None
    importance = None
    try:
        importance = journal.latest_feature_importance(symbol)
    except Exception:  # optional history may predate the feature table
        warnings.append("FEATURE_HISTORY_UNAVAILABLE")

    last_retrain = None
    last_promotion = None
    try:
        for event in da.retrain_events(db_path, limit=500):
            if event.get("symbol") not in (symbol, None):
                continue
            if last_retrain is None:
                last_retrain = event
            if event.get("promoted") and last_promotion is None:
                last_promotion = event
            if last_retrain is not None and last_promotion is not None:
                break
    except Exception:
        warnings.append("RETRAIN_HISTORY_UNAVAILABLE")

    has_inference = decision is not None
    has_training = last_retrain is not None
    if warnings:
        state = "DEGRADED"
    elif has_inference and has_training:
        state = "ACTIVE"
    elif has_inference or has_training:
        state = "PARTIAL_EVIDENCE"
    else:
        state = "NO_EVIDENCE"

    return {
        "state": state,
        "source": "LEGACY_WALK_FORWARD_LEARNING_AGENT",
        "timeframe": timeframe,
        "last_training_attempt_ms": _epoch_ms(
            last_retrain.get("timestamp") if last_retrain else None
        ),
        "last_promotion_ms": _epoch_ms(
            last_promotion.get("timestamp") if last_promotion else None
        ),
        "last_inference_ms": _epoch_ms(
            decision.get("timestamp") if decision else None
        ),
        "feature_count": len((importance or {}).get("importance", {})),
        "retrain_interval_hours": float(retrain_interval_hours),
        "warnings": warnings,
        "execution_authority": True,
        "promotion_authority": True,
    }


def _forecast_health(symbol: str, timeframe: str) -> Dict[str, Any]:
    from webapp.forecast import get_forecast_health

    return get_forecast_health(symbol, timeframe)


def _tournament_report(db_path: str, symbol: str) -> Dict[str, Any]:
    from webapp.quant_tournament import QuantTournamentBook

    return QuantTournamentBook(db_path).report(symbol)


def build_learning_health(
    db_path: str,
    symbol: str,
    *,
    tournament_db_path: Optional[str] = None,
    forecast_timeframe: str,
    legacy_timeframe: str,
    retrain_interval_hours: float,
) -> Dict[str, Any]:
    """Return safe aggregate telemetry without mutating any predictive path.

    ``db_path`` is the execution journal used by the legacy learner, while
    ``tournament_db_path`` is the dashboard analytics database that owns the
    rolling tournament model.  Keeping these paths explicit prevents an empty
    legacy database from impersonating an idle Tournament ML path.  The
    optional fallback preserves direct callers that deliberately colocate the
    two schemas (including isolated tests).
    """
    symbol = str(symbol).upper()
    path_warnings = []
    try:
        legacy = _legacy_health(
            db_path,
            symbol,
            timeframe=legacy_timeframe,
            retrain_interval_hours=retrain_interval_hours,
        )
    except Exception:
        legacy = {
            "state": "DEGRADED",
            "source": "LEGACY_WALK_FORWARD_LEARNING_AGENT",
            "warnings": ["LEGACY_TELEMETRY_UNAVAILABLE"],
            "execution_authority": True,
            "promotion_authority": True,
        }
        path_warnings.append("LEGACY_TELEMETRY_UNAVAILABLE")

    forecast = _forecast_health(symbol, forecast_timeframe)
    forecast.update(
        {
            "source": "NEXT_THREE_CANDLES_RANDOM_FOREST",
            "timeframe": forecast_timeframe,
            "execution_authority": False,
            "promotion_authority": False,
        }
    )

    tournament_report = _tournament_report(
        tournament_db_path or db_path,
        symbol,
    )
    tournament = tournament_report.get("learning", {}).get(
        "tournament_ml",
        {
            "source": "ROLLING_FORWARD_ONLY_LOGISTIC_ENSEMBLE",
            "horizons": {},
        },
    )
    tournament = dict(tournament)
    tournament.update(
        {
            "execution_authority": False,
            "promotion_authority": True,
        }
    )

    states = [legacy.get("state"), forecast.get("state")]
    states.extend(
        item.get("state")
        for item in tournament.get("horizons", {}).values()
    )
    overall = (
        "DEGRADED"
        if "DEGRADED" in states
        else "ACTIVE"
        if any(state in {"ACTIVE", "READY"} for state in states)
        else "WARMING"
    )
    return {
        "version": OBSERVABILITY_VERSION,
        "symbol": symbol,
        "state": overall,
        "paths": {
            "legacy_learning": legacy,
            "forecast_ml": forecast,
            "tournament_ml": tournament,
        },
        "separation_contract": {
            "paths_are_independent": True,
            "health_is_not_inferred_from_process_error_rate": True,
            "interpreter_is_read_only": True,
            "interpreter_execution_authority": False,
            "interpreter_promotion_authority": False,
        },
        "warnings": path_warnings,
    }
