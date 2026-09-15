"""CircuitBreaker: a persistent kill switch that halts new position entries
after repeated execution failures or a breached daily loss limit.

Tripped state is persisted to the journal so a process restart (e.g. under
a Docker restart policy) does not silently clear a safety trip - resuming
trading after a serious event must be a deliberate operator action, not an
accident of infrastructure restarting the process.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, Optional

from trading_assistant.agents.alerting import AlertManager
from trading_assistant.persistence.journal import Journal

logger = logging.getLogger(__name__)


class CircuitBreaker:
    def __init__(
        self,
        max_consecutive_errors: int = 3,
        journal: Optional[Journal] = None,
        alert_manager: Optional[AlertManager] = None,
    ):
        self.max_consecutive_errors = max_consecutive_errors
        self.journal = journal
        # A trip means new positions have stopped opening system-wide -
        # exactly the kind of event that should reach a human promptly
        # rather than wait to be noticed in a log file. None (the default)
        # means no alert is raised - existing callers/tests are unaffected.
        self.alert_manager = alert_manager
        # Keyed by symbol - a single shared counter would let one symbol's
        # successes reset another symbol's (or its own alternating) failures,
        # so a permanently broken symbol could never accumulate enough
        # consecutive errors in a row to trip the breaker.
        self._consecutive_errors: Dict[str, int] = {}
        self.tripped = False
        self.trip_reason: Optional[str] = None
        self.tripped_at: Optional[str] = None

        if self.journal is not None:
            self._restore_from_journal()

    def _restore_from_journal(self) -> None:
        state = self.journal.latest_circuit_breaker_event()
        if state and state["tripped"]:
            self.tripped = True
            self.trip_reason = state["reason"]
            self.tripped_at = state["timestamp"]
            logger.warning(
                "circuit breaker restored as tripped from a previous run",
                extra={"context": {"reason": self.trip_reason}},
            )

    def record_result(self, symbol: str, success: bool) -> None:
        """Feed the outcome of an execution attempt for one symbol. Trips
        (system-wide) after max_consecutive_errors in a row for that symbol;
        a success resets only that symbol's counter."""
        if success:
            self._consecutive_errors[symbol] = 0
            return
        count = self._consecutive_errors.get(symbol, 0) + 1
        self._consecutive_errors[symbol] = count
        if count >= self.max_consecutive_errors and not self.tripped:
            self.trip(f"{count} consecutive execution errors on {symbol}")

    def trip(self, reason: str) -> None:
        if self.tripped:
            return
        self.tripped = True
        self.trip_reason = reason
        self.tripped_at = datetime.now(timezone.utc).isoformat()
        logger.error("circuit breaker tripped", extra={"context": {"reason": reason}})
        if self.journal is not None:
            self.journal.record_circuit_breaker_event(tripped=True, reason=reason)
        if self.alert_manager is not None:
            self.alert_manager.raise_alert(
                "circuit_breaker", "critical", f"circuit breaker tripped: {reason}", context={"reason": reason}
            )

    def reset(self, operator: Optional[str] = None) -> None:
        """Deliberately clear an active trip and persist the recovery."""
        if not self.tripped:
            logger.info(
                "circuit breaker reset requested while already clear",
                extra={"context": {"operator": operator}},
            )
            return

        previous_reason = self.trip_reason
        previous_tripped_at = self.tripped_at

        logger.warning(
            "circuit breaker reset",
            extra={
                "context": {
                    "operator": operator,
                    "previous_reason": previous_reason,
                }
            },
        )

        self.tripped = False
        self.trip_reason = None
        self.tripped_at = None
        self._consecutive_errors = {}

        if self.journal is not None:
            self.journal.record_circuit_breaker_event(
                tripped=False,
                reason=f"reset by {operator}" if operator else "reset",
            )

        if self.alert_manager is not None:
            incident_key = previous_tripped_at or previous_reason or "unknown"
            self.alert_manager.raise_alert(
                "circuit_breaker_recovery",
                "info",
                "circuit breaker reset; trading safety hold has been cleared",
                context={
                    "operator": operator,
                    "previous_reason": previous_reason,
                    "previous_tripped_at": previous_tripped_at,
                },
                dedupe_key=f"circuit_breaker_recovery:{incident_key}",
                force_email=True,
            )
