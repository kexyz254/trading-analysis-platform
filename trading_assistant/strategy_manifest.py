"""Immutable strategy-manifest contract for shadow price-call consumers.

This module is deliberately runtime-disconnected: importing it has no side
effects, opens no database, performs no network I/O, and cannot place orders.
"""

from __future__ import annotations

from dataclasses import MISSING, dataclass, fields
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Mapping
import hmac
import json
import math
import os
import re


SCHEMA_VERSION = 1
ALLOWED_HORIZONS = frozenset({"5m", "15m"})
ALLOWED_STRATEGY_KINDS = frozenset({"ml", "rule"})
ALLOWED_STATUSES = frozenset({"active", "suspended", "retired"})

_SYMBOL_RE = re.compile(
    r"^[A-Z0-9][A-Z0-9._-]*/[A-Z0-9][A-Z0-9._-]*$"
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GIT_COMMIT_RE = re.compile(
    r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$"
)


class ManifestError(Exception):
    """Base exception for strategy-manifest failures."""


class ManifestValidationError(ManifestError, ValueError):
    """Raised when manifest metadata violates the contract."""


class ManifestIntegrityError(ManifestError):
    """Raised when the declared artifact cannot be verified."""


class ManifestStateError(ManifestError):
    """Raised when a non-active strategy is requested for issuance."""


def sha256_file(
    path: Path,
    chunk_size: int = 1024 * 1024,
) -> str:
    """Return the lowercase SHA-256 digest of one regular file."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")

    digest = sha256()

    with Path(path).open("rb") as handle:
        for chunk in iter(
            lambda: handle.read(chunk_size),
            b"",
        ):
            digest.update(chunk)

    return digest.hexdigest()


def _required_text(
    field_name: str,
    value: Any,
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ManifestValidationError(
            f"{field_name} must be a non-empty string"
        )

    normalized = value.strip()

    if any(ord(character) < 32 for character in normalized):
        raise ManifestValidationError(
            f"{field_name} contains control characters"
        )

    return normalized


def _normalized_utc(
    field_name: str,
    value: Any,
) -> tuple[str, datetime]:
    raw = _required_text(field_name, value)
    candidate = (
        raw[:-1] + "+00:00"
        if raw.endswith("Z")
        else raw
    )

    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ManifestValidationError(
            f"{field_name} must be valid ISO-8601"
        ) from exc

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ManifestValidationError(
            f"{field_name} must include an explicit timezone"
        )

    utc_value = parsed.astimezone(timezone.utc)
    canonical = utc_value.isoformat().replace(
        "+00:00",
        "Z",
    )

    return canonical, utc_value


def _freeze_json(
    value: Any,
    field_name: str,
) -> Any:
    if isinstance(value, Mapping):
        frozen = {}

        for key, child in value.items():
            if not isinstance(key, str):
                raise ManifestValidationError(
                    f"{field_name} keys must all be strings"
                )

            frozen[key] = _freeze_json(
                child,
                field_name,
            )

        return MappingProxyType(frozen)

    if isinstance(value, (list, tuple)):
        return tuple(
            _freeze_json(child, field_name)
            for child in value
        )

    if value is None or isinstance(
        value,
        (str, bool, int),
    ):
        return value

    if isinstance(value, float):
        if not math.isfinite(value):
            raise ManifestValidationError(
                f"{field_name} cannot contain NaN or infinity"
            )

        return value

    raise ManifestValidationError(
        f"{field_name} contains unsupported value type "
        f"{type(value).__name__}"
    )


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: _thaw_json(child)
            for key, child in value.items()
        }

    if isinstance(value, tuple):
        return [
            _thaw_json(child)
            for child in value
        ]

    return value


def _relative_artifact_path(value: Any) -> str:
    raw = _required_text(
        "artifact_path",
        value,
    )

    if "\\" in raw:
        raise ManifestValidationError(
            "artifact_path must use forward slashes"
        )

    path = PurePosixPath(raw)

    if path.is_absolute() or ".." in path.parts:
        raise ManifestValidationError(
            "artifact_path must be relative and cannot contain '..'"
        )

    if (
        not path.parts
        or any(part in ("", ".") for part in path.parts)
    ):
        raise ManifestValidationError(
            "artifact_path must identify a concrete relative file"
        )

    return path.as_posix()


@dataclass(frozen=True, slots=True)
class StrategyManifest:
    """Immutable identity and promotion evidence for one strategy artifact."""

    schema_version: int
    symbol: str
    horizon: str
    strategy_name: str
    strategy_kind: str
    strategy_version: str
    artifact_path: str
    artifact_sha256: str
    feature_schema_sha256: str
    training_timeframe: str
    prediction_horizon_bars: int
    training_data_cutoff: str
    promoted_at: str
    source_commit: str
    promotion_metrics: Mapping[str, Any]
    decision_thresholds: Mapping[str, Any]
    status: str = "active"

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ManifestValidationError(
                f"schema_version must equal {SCHEMA_VERSION}"
            )

        symbol = _required_text(
            "symbol",
            self.symbol,
        ).upper()

        if not _SYMBOL_RE.fullmatch(symbol):
            raise ManifestValidationError(
                "symbol must use canonical BASE/QUOTE form"
            )

        horizon = _required_text(
            "horizon",
            self.horizon,
        )

        if horizon not in ALLOWED_HORIZONS:
            raise ManifestValidationError(
                f"horizon must be one of "
                f"{sorted(ALLOWED_HORIZONS)}"
            )

        strategy_kind = _required_text(
            "strategy_kind",
            self.strategy_kind,
        ).lower()

        if strategy_kind not in ALLOWED_STRATEGY_KINDS:
            raise ManifestValidationError(
                "strategy_kind must be 'ml' or 'rule'"
            )

        status = _required_text(
            "status",
            self.status,
        ).lower()

        if status not in ALLOWED_STATUSES:
            raise ManifestValidationError(
                f"status must be one of "
                f"{sorted(ALLOWED_STATUSES)}"
            )

        training_timeframe = _required_text(
            "training_timeframe",
            self.training_timeframe,
        )

        if training_timeframe != horizon:
            raise ManifestValidationError(
                "training_timeframe must match the prediction horizon"
            )

        if (
            isinstance(self.prediction_horizon_bars, bool)
            or self.prediction_horizon_bars != 1
        ):
            raise ManifestValidationError(
                "prediction_horizon_bars must equal 1"
            )

        artifact_sha256 = _required_text(
            "artifact_sha256",
            self.artifact_sha256,
        ).lower()

        feature_schema_sha256 = _required_text(
            "feature_schema_sha256",
            self.feature_schema_sha256,
        ).lower()

        if not _SHA256_RE.fullmatch(artifact_sha256):
            raise ManifestValidationError(
                "artifact_sha256 must be 64 lowercase "
                "hexadecimal characters"
            )

        if not _SHA256_RE.fullmatch(
            feature_schema_sha256
        ):
            raise ManifestValidationError(
                "feature_schema_sha256 must be 64 lowercase "
                "hexadecimal characters"
            )

        source_commit = _required_text(
            "source_commit",
            self.source_commit,
        ).lower()

        if not _GIT_COMMIT_RE.fullmatch(source_commit):
            raise ManifestValidationError(
                "source_commit must be a full 40- or "
                "64-character Git object ID"
            )

        cutoff_text, cutoff = _normalized_utc(
            "training_data_cutoff",
            self.training_data_cutoff,
        )

        promoted_text, promoted = _normalized_utc(
            "promoted_at",
            self.promoted_at,
        )

        if cutoff > promoted:
            raise ManifestValidationError(
                "training_data_cutoff cannot be later than promoted_at"
            )

        metrics = _freeze_json(
            self.promotion_metrics,
            "promotion_metrics",
        )

        thresholds = _freeze_json(
            self.decision_thresholds,
            "decision_thresholds",
        )

        if not metrics:
            raise ManifestValidationError(
                "promotion_metrics cannot be empty"
            )

        if not thresholds:
            raise ManifestValidationError(
                "decision_thresholds cannot be empty"
            )

        object.__setattr__(
            self,
            "symbol",
            symbol,
        )
        object.__setattr__(
            self,
            "horizon",
            horizon,
        )
        object.__setattr__(
            self,
            "strategy_name",
            _required_text(
                "strategy_name",
                self.strategy_name,
            ),
        )
        object.__setattr__(
            self,
            "strategy_version",
            _required_text(
                "strategy_version",
                self.strategy_version,
            ),
        )
        object.__setattr__(
            self,
            "artifact_path",
            _relative_artifact_path(
                self.artifact_path
            ),
        )
        object.__setattr__(
            self,
            "artifact_sha256",
            artifact_sha256,
        )
        object.__setattr__(
            self,
            "feature_schema_sha256",
            feature_schema_sha256,
        )
        object.__setattr__(
            self,
            "training_timeframe",
            training_timeframe,
        )
        object.__setattr__(
            self,
            "training_data_cutoff",
            cutoff_text,
        )
        object.__setattr__(
            self,
            "promoted_at",
            promoted_text,
        )
        object.__setattr__(
            self,
            "source_commit",
            source_commit,
        )
        object.__setattr__(
            self,
            "strategy_kind",
            strategy_kind,
        )
        object.__setattr__(
            self,
            "promotion_metrics",
            metrics,
        )
        object.__setattr__(
            self,
            "decision_thresholds",
            thresholds,
        )
        object.__setattr__(
            self,
            "status",
            status,
        )

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "StrategyManifest":
        if not isinstance(payload, Mapping):
            raise ManifestValidationError(
                "manifest payload must be a JSON object"
            )

        manifest_fields = {
            field.name: field
            for field in fields(cls)
        }

        allowed = set(manifest_fields)
        supplied = set(payload)

        unknown = sorted(supplied - allowed)

        if unknown:
            raise ManifestValidationError(
                f"unknown manifest fields: {unknown}"
            )

        required = {
            name
            for name, field in manifest_fields.items()
            if field.default is MISSING
            and field.default_factory is MISSING
        }

        missing = sorted(required - supplied)

        if missing:
            raise ManifestValidationError(
                f"missing manifest fields: {missing}"
            )

        return cls(**dict(payload))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "symbol": self.symbol,
            "horizon": self.horizon,
            "strategy_name": self.strategy_name,
            "strategy_kind": self.strategy_kind,
            "strategy_version": self.strategy_version,
            "artifact_path": self.artifact_path,
            "artifact_sha256": self.artifact_sha256,
            "feature_schema_sha256": (
                self.feature_schema_sha256
            ),
            "training_timeframe": self.training_timeframe,
            "prediction_horizon_bars": (
                self.prediction_horizon_bars
            ),
            "training_data_cutoff": (
                self.training_data_cutoff
            ),
            "promoted_at": self.promoted_at,
            "source_commit": self.source_commit,
            "promotion_metrics": _thaw_json(
                self.promotion_metrics
            ),
            "decision_thresholds": _thaw_json(
                self.decision_thresholds
            ),
            "status": self.status,
        }

    def canonical_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )

    def manifest_sha256(self) -> str:
        return sha256(
            self.canonical_json().encode("utf-8")
        ).hexdigest()

    def resolve_artifact(
        self,
        artifact_root: Path,
        *,
        require_exists: bool = True,
    ) -> Path:
        root = Path(
            artifact_root
        ).expanduser().resolve()

        if not root.is_dir():
            raise ManifestIntegrityError(
                "artifact root does not exist or is not "
                f"a directory: {root}"
            )

        candidate = (
            root / self.artifact_path
        ).resolve(strict=False)

        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ManifestIntegrityError(
                "resolved artifact path escapes the artifact root"
            ) from exc

        if require_exists and not candidate.is_file():
            raise ManifestIntegrityError(
                f"strategy artifact is missing: {candidate}"
            )

        return candidate

    def verify_artifact(
        self,
        artifact_root: Path,
    ) -> Path:
        artifact = self.resolve_artifact(
            artifact_root
        )

        actual = sha256_file(artifact)

        if not hmac.compare_digest(
            actual,
            self.artifact_sha256,
        ):
            raise ManifestIntegrityError(
                "strategy artifact SHA-256 does not "
                "match the manifest"
            )

        return artifact

    def assert_issuable(
        self,
        artifact_root: Path,
    ) -> Path:
        if self.status != "active":
            raise ManifestStateError(
                f"strategy status is {self.status!r}, "
                "not 'active'"
            )

        return self.verify_artifact(
            artifact_root
        )


def load_manifest(path: Path) -> StrategyManifest:
    try:
        payload = json.loads(
            Path(path).read_text(
                encoding="utf-8"
            )
        )
    except (
        OSError,
        json.JSONDecodeError,
    ) as exc:
        raise ManifestValidationError(
            f"could not load strategy manifest: {path}"
        ) from exc

    return StrategyManifest.from_dict(
        payload
    )


def save_manifest(
    manifest: StrategyManifest,
    path: Path,
) -> Path:
    destination = Path(path)
    destination.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary = destination.with_name(
        f".{destination.name}.{os.getpid()}.tmp"
    )

    try:
        temporary.write_text(
            manifest.canonical_json() + "\n",
            encoding="utf-8",
        )
        os.replace(
            temporary,
            destination,
        )
    finally:
        if temporary.exists():
            temporary.unlink()

    return destination
