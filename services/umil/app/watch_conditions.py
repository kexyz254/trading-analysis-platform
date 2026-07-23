"""Dynamic, evidence-derived watch conditions and lifecycle transitions."""

from __future__ import annotations

import operator
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from enum import Enum
from typing import Mapping

from .features import TimeframeFeatures


class WatchState(str, Enum):
    ACTIVE = "ACTIVE"
    TRIGGERED = "TRIGGERED"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"


_OPERATORS = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le}


@dataclass(frozen=True, slots=True)
class WatchCondition:
    condition_id: str
    instrument_id: str
    created_at: datetime
    expires_at: datetime
    state: WatchState
    metric: str
    comparison: str
    threshold: float
    invalidation_metric: str | None
    invalidation_comparison: str | None
    invalidation_threshold: float | None
    interpretation: str
    source_as_of: str
    evidence_ids: tuple[str, ...] = ()
    resolved_at: datetime | None = None

    def as_dict(self) -> dict[str, object]:
        row = asdict(self)
        row["state"] = self.state.value
        row["evidence_ids"] = list(self.evidence_ids)
        return row


def create_structure_watches(
    instrument_id: str,
    feature: TimeframeFeatures,
    *,
    current_price: float,
    range_high: float,
    range_low: float,
    created_at: datetime,
    ttl: timedelta = timedelta(hours=6),
    evidence_ids: tuple[str, ...] = (),
) -> tuple[WatchCondition, ...]:
    if not 0 < range_low <= current_price <= range_high:
        raise ValueError("price must be inside the source range")
    common = {
        "instrument_id": instrument_id, "created_at": created_at,
        "expires_at": created_at + ttl, "state": WatchState.ACTIVE,
        "source_as_of": feature.as_of, "evidence_ids": evidence_ids,
        "resolved_at": None,
    }
    return (
        WatchCondition(
            condition_id=str(uuid.uuid4()), metric="last_price", comparison=">",
            threshold=range_high, invalidation_metric="last_price",
            invalidation_comparison="<", invalidation_threshold=range_low,
            interpretation="A confirmed range breakout would strengthen trend structure.",
            **common,
        ),
        WatchCondition(
            condition_id=str(uuid.uuid4()), metric="last_price", comparison="<",
            threshold=range_low, invalidation_metric="last_price",
            invalidation_comparison=">", invalidation_threshold=range_high,
            interpretation="A confirmed range breakdown would weaken market structure.",
            **common,
        ),
    )


def evaluate_condition(
    condition: WatchCondition, values: Mapping[str, float], *, as_of: datetime,
) -> WatchCondition:
    if condition.state is not WatchState.ACTIVE:
        return condition
    if as_of >= condition.expires_at:
        return replace(condition, state=WatchState.EXPIRED, resolved_at=as_of)
    value = values.get(condition.metric)
    if value is not None and _OPERATORS[condition.comparison](value, condition.threshold):
        return replace(condition, state=WatchState.TRIGGERED, resolved_at=as_of)
    invalid_value = values.get(condition.invalidation_metric or "")
    if (
        invalid_value is not None
        and condition.invalidation_comparison is not None
        and condition.invalidation_threshold is not None
        and _OPERATORS[condition.invalidation_comparison](
            invalid_value, condition.invalidation_threshold
        )
    ):
        return replace(condition, state=WatchState.INVALIDATED, resolved_at=as_of)
    return condition


def condition_from_dict(value: Mapping[str, object]) -> WatchCondition:
    def timestamp(name: str) -> datetime:
        raw = value.get(name)
        if not isinstance(raw, str):
            raise ValueError(f"invalid {name}")
        result = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError(f"naive {name}")
        return result
    resolved = value.get("resolved_at")
    resolved_at = None
    if isinstance(resolved, str):
        resolved_at = datetime.fromisoformat(resolved.replace("Z", "+00:00"))
    return WatchCondition(
        condition_id=str(value["condition_id"]),
        instrument_id=str(value["instrument_id"]),
        created_at=timestamp("created_at"), expires_at=timestamp("expires_at"),
        state=WatchState(str(value["state"])), metric=str(value["metric"]),
        comparison=str(value["comparison"]), threshold=float(value["threshold"]),
        invalidation_metric=str(value["invalidation_metric"]) if value.get("invalidation_metric") is not None else None,
        invalidation_comparison=str(value["invalidation_comparison"]) if value.get("invalidation_comparison") is not None else None,
        invalidation_threshold=float(value["invalidation_threshold"]) if value.get("invalidation_threshold") is not None else None,
        interpretation=str(value["interpretation"]),
        source_as_of=str(value["source_as_of"]),
        evidence_ids=tuple(str(item) for item in value.get("evidence_ids", ())),
        resolved_at=resolved_at,
    )
