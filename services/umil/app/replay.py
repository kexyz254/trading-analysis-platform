"""Point-in-time retrieval: answer exactly what UMIL knew at a past instant."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .evidence import EvidenceLedger, EvidenceRecord


@dataclass(frozen=True, slots=True)
class ReplayResult:
    requested_as_of: str
    knowledge_cutoff: str
    instrument_id: str
    event_type: str
    evidence: dict[str, Any] | None
    exact: bool


class HistoricalReplay:
    def __init__(self, ledger: EvidenceLedger) -> None:
        self.ledger = ledger

    def knowledge_at(
        self, instrument_id: str, *, as_of: datetime,
        event_type: str = "MARKET_INTELLIGENCE_SNAPSHOT",
    ) -> ReplayResult:
        if as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")
        row = self.ledger.latest_as_of(
            event_type=event_type, instrument_id=instrument_id, as_of=as_of,
        )
        return ReplayResult(
            requested_as_of=as_of.astimezone(timezone.utc).isoformat(),
            knowledge_cutoff=as_of.astimezone(timezone.utc).isoformat(),
            instrument_id=instrument_id, event_type=event_type,
            evidence=row.as_dict() if row else None,
            exact=bool(row and row.observed_at == as_of.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")),
        )
