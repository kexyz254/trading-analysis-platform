"""System health score: turns "is the live engine actually okay right now"
into a computed, auditable answer instead of an operator eyeballing scattered
log lines.

Same design as trading_assistant/readiness.py: a pure function of data
already persisted in the journal (system_heartbeat, circuit_breaker_events),
so a separate process (the dashboard) can compute the identical score a
human would get by inspecting the running engine directly - the engine and
the dashboard are two separate OS processes that only ever communicate
through what's written to the journal.

Never returns a fabricated number: if no heartbeat has been recorded yet
(an old database, or an engine process that hasn't been restarted since
this feature shipped), the score is explicitly None/"unknown" rather than
some default like 100.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Point deductions are deliberately weighted so a tripped circuit breaker
# alone is enough to read as "critical" (60 + nothing else already crosses
# the <50 critical threshold below), while staleness or a moderate error
# rate alone only reads as "degraded", not "critical" - a stale cycle could
# be a slow network blip, not necessarily a broken engine.
_CIRCUIT_BREAKER_PENALTY = 60.0
_STALENESS_PENALTY = 30.0
_ERROR_RATE_PENALTY_WEIGHT = 40.0

_HEALTHY_THRESHOLD = 85.0
_DEGRADED_THRESHOLD = 50.0


@dataclass
class HealthReport:
    score: Optional[float]
    label: str
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"score": self.score, "label": self.label, "reasons": self.reasons}


def compute_health_score(
    *,
    has_heartbeat: bool,
    circuit_breaker_tripped: bool = False,
    staleness_seconds: Optional[float] = None,
    poll_interval_seconds: float = 60.0,
    recent_error_rate: Optional[float] = None,
) -> HealthReport:
    """Pure function - no I/O, no clock reads. Callers resolve
    `staleness_seconds`/`recent_error_rate` from the journal before calling
    this, so the formula itself stays trivially testable.

    `recent_error_rate` is the fraction (0.0-1.0) of the last N heartbeat
    cycles that recorded status='error'.
    """
    if not has_heartbeat:
        return HealthReport(score=None, label="unknown", reasons=["no heartbeat data recorded yet"])

    score = 100.0
    reasons: List[str] = []

    if circuit_breaker_tripped:
        score -= _CIRCUIT_BREAKER_PENALTY
        reasons.append("circuit breaker tripped")

    if staleness_seconds is not None and staleness_seconds > poll_interval_seconds * 3:
        score -= _STALENESS_PENALTY
        reasons.append(
            f"last cycle {int(staleness_seconds)}s ago, expected roughly every {int(poll_interval_seconds)}s"
        )

    if recent_error_rate:
        score -= recent_error_rate * _ERROR_RATE_PENALTY_WEIGHT
        reasons.append(f"{int(round(recent_error_rate * 100))}% of recent cycles errored")

    score = max(0.0, min(100.0, score))
    if score >= _HEALTHY_THRESHOLD:
        label = "healthy"
    elif score >= _DEGRADED_THRESHOLD:
        label = "degraded"
    else:
        label = "critical"

    return HealthReport(score=round(score), label=label, reasons=reasons)
