"""Dynamic registry deciding which markets deserve expensive live intelligence."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Set


VALID_REASONS = {
    "selected",
    "watchlist",
    "manual_order",
    "manual_position",
    "ats_candidate",
    "ats_position",
    "open_order",
    "account_holding",
}

TRANSIENT_REASONS = {"selected", "ats_candidate"}


class TrackedPairRegistry:
    def __init__(self) -> None:
        self._tracked: Dict[str, Dict[str, datetime]] = {}

    def track(
        self,
        symbol: str,
        reason: str,
        *,
        now: Optional[datetime] = None,
    ) -> None:
        if reason not in VALID_REASONS:
            raise ValueError(f"unsupported tracking reason: {reason}")

        timestamp = now or datetime.now(timezone.utc)
        self._tracked.setdefault(symbol, {})[reason] = timestamp

    def untrack(self, symbol: str, reason: str) -> None:
        reasons = self._tracked.get(symbol)
        if not reasons:
            return

        reasons.pop(reason, None)
        if not reasons:
            self._tracked.pop(symbol, None)

    def reasons(self, symbol: str) -> Set[str]:
        return set(self._tracked.get(symbol, {}))

    def symbols(self) -> List[str]:
        return sorted(self._tracked)

    def is_tracked(self, symbol: str) -> bool:
        return symbol in self._tracked

    def track_many(self, symbols: Iterable[str], reason: str) -> None:
        for symbol in symbols:
            self.track(symbol, reason)

    def expire_transient(
        self,
        max_age_seconds: float,
        *,
        now: Optional[datetime] = None,
    ) -> None:
        current = now or datetime.now(timezone.utc)

        for symbol in list(self._tracked):
            reasons = self._tracked[symbol]

            for reason in list(reasons):
                if reason not in TRANSIENT_REASONS:
                    continue

                age = (current - reasons[reason]).total_seconds()
                if age >= max_age_seconds:
                    reasons.pop(reason)

            if not reasons:
                self._tracked.pop(symbol)

    def snapshot(self) -> Dict[str, List[str]]:
        return {
            symbol: sorted(reasons)
            for symbol, reasons in sorted(self._tracked.items())
        }
