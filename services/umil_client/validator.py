"""Validate UMIL as an untrusted advisory dependency.

This package deliberately does not import ``services.umil.app``.  Compatibility
is owned by the consumer and must be changed explicitly when UMIL evolves.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping


_ALLOWED_API_SCHEMAS = frozenset({"umil-api-v1", "umil-api-v2"})
_ALLOWED_SNAPSHOT_SCHEMAS = frozenset({"umil-snapshot-v1", "umil-snapshot-v2"})
_ALLOWED_FEATURE_SCHEMAS = frozenset({"umil-features-v1", "umil-features-v2"})
_ALLOWED_QUALITY_SCHEMAS = frozenset({"umil-quality-v1", "umil-quality-v2"})
_ALLOWED_DNA_SCHEMAS = frozenset({"umil-dna-v2"})
_ALLOWED_STATES = frozenset({
    "WARMING", "READY", "DEGRADED", "STALE", "INVALID", "UNAVAILABLE"
})
_REQUIRED_FALSE_AUTHORITY = frozenset({
    "can_trade", "can_cancel_orders", "can_access_trading_credentials",
    "can_train", "can_influence_ats", "can_modify_risk",
    "can_modify_tournament", "can_write_ats_storage",
})


@dataclass(frozen=True, slots=True)
class ValidationResult:
    accepted: bool
    reason: str | None
    snapshot: Mapping[str, Any] | None = None


class SnapshotValidator:
    def __init__(self, *, max_age_seconds: float = 20.0, max_future_skew_seconds: float = 2.0) -> None:
        if max_age_seconds <= 0 or max_future_skew_seconds < 0:
            raise ValueError("invalid snapshot timing policy")
        self.max_age_seconds = max_age_seconds
        self.max_future_skew_seconds = max_future_skew_seconds

    def validate(
        self,
        value: Any,
        *,
        received_at: datetime | None = None,
    ) -> ValidationResult:
        received_at = received_at or datetime.now(timezone.utc)
        if received_at.tzinfo is None:
            return ValidationResult(False, "CONSUMER_CLOCK_IS_NAIVE")
        if not isinstance(value, Mapping):
            return ValidationResult(False, "MALFORMED_ROOT")
        try:
            self._validate_versions(value)
            as_of = self._timestamp(value.get("as_of"))
            age = (received_at - as_of).total_seconds()
            if age < -self.max_future_skew_seconds:
                raise ValueError("FUTURE_DATED_SNAPSHOT")
            if age > self.max_age_seconds:
                raise ValueError("STALE_SNAPSHOT")
            state = value.get("state")
            if state not in _ALLOWED_STATES:
                raise ValueError("UNSUPPORTED_STATE")
            if state in {"INVALID", "UNAVAILABLE"}:
                raise ValueError(f"UPSTREAM_{state}")
            self._validate_authority(value.get("authority"))
            self._validate_quality(value.get("quality"))
            self._validate_breadth(value)
            evidence_ids = value.get("evidence_ids")
            if not isinstance(evidence_ids, list) or not evidence_ids:
                raise ValueError("MISSING_EVIDENCE_LINEAGE")
        except (KeyError, TypeError, ValueError) as exc:
            return ValidationResult(False, str(exc) or type(exc).__name__)
        return ValidationResult(True, None, dict(value))

    @staticmethod
    def _validate_versions(value: Mapping[str, Any]) -> None:
        policies = (
            ("api_schema_version", _ALLOWED_API_SCHEMAS),
            ("snapshot_schema_version", _ALLOWED_SNAPSHOT_SCHEMAS),
            ("feature_schema_version", _ALLOWED_FEATURE_SCHEMAS),
            ("quality_schema_version", _ALLOWED_QUALITY_SCHEMAS),
        )
        for field, allowed in policies:
            if value.get(field) not in allowed:
                raise ValueError(f"INCOMPATIBLE_{field.upper()}")

    @staticmethod
    def _validate_authority(value: Any) -> None:
        if not isinstance(value, Mapping) or value.get("read_only") is not True:
            raise ValueError("AUTHORITY_NOT_READ_ONLY")
        if any(value.get(field) is not False for field in _REQUIRED_FALSE_AUTHORITY):
            raise ValueError("AUTHORITY_ESCALATION")

    @classmethod
    def _validate_quality(cls, value: Any) -> None:
        if not isinstance(value, Mapping):
            raise ValueError("MISSING_QUALITY")
        cls._bounded(value.get("completeness"), 0.0, 1.0, "QUALITY_COMPLETENESS")
        cls._bounded(value.get("synchronization_quality"), 0.0, 1.0, "SYNC_QUALITY")
        cls._bounded(value.get("freshness_percentile"), 0.0, 100.0, "FRESHNESS_PERCENTILE")
        latencies = value.get("provider_latency_ms")
        if not isinstance(latencies, Mapping):
            raise ValueError("MISSING_PROVIDER_LATENCY")
        if any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in latencies.values()):
            raise ValueError("INVALID_PROVIDER_LATENCY")
        for field in ("missing_providers", "stale_dimensions", "quality_flags"):
            if not isinstance(value.get(field), list):
                raise ValueError(f"INVALID_{field.upper()}")

    @classmethod
    def _validate_breadth(cls, value: Mapping[str, Any]) -> None:
        count_fields = (
            "eligible_instruments", "observed_instruments", "advancers",
            "decliners", "unchanged",
        )
        counts: dict[str, int] = {}
        for field in count_fields:
            item = value.get(field)
            if not isinstance(item, int) or isinstance(item, bool) or item < 0:
                raise ValueError(f"INVALID_{field.upper()}")
            counts[field] = item
        if counts["observed_instruments"] > counts["eligible_instruments"]:
            raise ValueError("OBSERVED_EXCEEDS_UNIVERSE")
        if counts["advancers"] + counts["decliners"] + counts["unchanged"] != counts["observed_instruments"]:
            raise ValueError("BREADTH_COUNTS_DO_NOT_RECONCILE")
        for field in ("advance_ratio", "volume_weighted_advance_ratio"):
            item = value.get(field)
            if item is not None:
                cls._bounded(item, 0.0, 1.0, field.upper())
        item = value.get("volume_concentration_hhi")
        if item is not None:
            cls._bounded(item, 0.0, 1.0, "VOLUME_CONCENTRATION_HHI")

    @staticmethod
    def _bounded(value: Any, lower: float, upper: float, label: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"INVALID_{label}")
        number = float(value)
        if not math.isfinite(number) or not lower <= number <= upper:
            raise ValueError(f"INVALID_{label}")
        return number

    @staticmethod
    def _timestamp(value: Any) -> datetime:
        if not isinstance(value, str):
            raise ValueError("INVALID_AS_OF")
        text = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            result = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError("INVALID_AS_OF") from exc
        if result.tzinfo is None:
            raise ValueError("NAIVE_AS_OF")
        return result.astimezone(timezone.utc)


class MarketIntelligenceValidator(SnapshotValidator):
    """Consumer-owned validator for the richer, separately versioned product API."""

    _dna_keys = frozenset({"TRN", "MOM", "PRS", "STR", "VOL", "LIQ", "ACT", "RSK"})

    def validate(
        self, value: Any, *, received_at: datetime | None = None,
    ) -> ValidationResult:
        received_at = received_at or datetime.now(timezone.utc)
        if not isinstance(value, Mapping):
            return ValidationResult(False, "MALFORMED_ROOT")
        try:
            versions = value.get("versions")
            if not isinstance(versions, Mapping):
                raise ValueError("MISSING_VERSIONS")
            if versions.get("api_schema_version") not in _ALLOWED_API_SCHEMAS:
                raise ValueError("INCOMPATIBLE_API_SCHEMA_VERSION")
            if versions.get("snapshot_schema_version") not in _ALLOWED_SNAPSHOT_SCHEMAS:
                raise ValueError("INCOMPATIBLE_SNAPSHOT_SCHEMA_VERSION")
            if versions.get("feature_schema_version") not in _ALLOWED_FEATURE_SCHEMAS:
                raise ValueError("INCOMPATIBLE_FEATURE_SCHEMA_VERSION")
            if versions.get("dna_schema_version") not in _ALLOWED_DNA_SCHEMAS:
                raise ValueError("INCOMPATIBLE_DNA_SCHEMA_VERSION")
            as_of = self._timestamp(value.get("as_of"))
            age = (received_at - as_of).total_seconds()
            if age < -self.max_future_skew_seconds:
                raise ValueError("FUTURE_DATED_SNAPSHOT")
            if age > self.max_age_seconds:
                raise ValueError("STALE_SNAPSHOT")
            if value.get("state") not in _ALLOWED_STATES:
                raise ValueError("UNSUPPORTED_STATE")
            self._validate_authority(value.get("authority"))
            self._bounded(value.get("clarity"), 0.0, 100.0, "CLARITY")
            self._bounded(value.get("confidence"), 0.0, 100.0, "CONFIDENCE")
            if value.get("directional_probability") is not None:
                raise ValueError("UNVALIDATED_DIRECTIONAL_PROBABILITY")
            dna = value.get("dna")
            if not isinstance(dna, Mapping) or set(dna) != self._dna_keys:
                raise ValueError("INVALID_DNA_SHAPE")
            for key, score in dna.items():
                self._bounded(score, 0.0, 100.0, f"DNA_{key}")
            dimensions = value.get("dimensions")
            if not isinstance(dimensions, Mapping) or set(dimensions) != self._dna_keys:
                raise ValueError("INVALID_DIMENSION_LINEAGE")
            evidence = value.get("evidence_ids")
            if not isinstance(evidence, list) or not evidence:
                raise ValueError("MISSING_EVIDENCE_LINEAGE")
            commentary = value.get("commentary")
            if not isinstance(commentary, Mapping) or not isinstance(commentary.get("claims"), list):
                raise ValueError("INVALID_COMMENTARY")
        except (KeyError, TypeError, ValueError) as exc:
            return ValidationResult(False, str(exc) or type(exc).__name__)
        return ValidationResult(True, None, dict(value))
