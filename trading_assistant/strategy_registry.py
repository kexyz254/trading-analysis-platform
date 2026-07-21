"""Read-only registry for immutable promoted-strategy manifests.

This module performs no database writes, network calls, model training,
model inference, or exchange execution. It only discovers, validates,
and verifies strategy manifests and their referenced artifacts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Optional
import re

from trading_assistant.strategy_manifest import (
    ALLOWED_HORIZONS,
    ManifestError,
    StrategyManifest,
    load_manifest,
)


_SYMBOL_RE = re.compile(
    r"^[A-Z0-9][A-Z0-9._-]*/[A-Z0-9][A-Z0-9._-]*$"
)


class StrategyRegistryError(Exception):
    """Base exception for strategy-registry failures."""


class StrategyNotFoundError(StrategyRegistryError):
    """No active strategy exists for the requested contract."""


class StrategyNotReadyError(StrategyRegistryError):
    """A strategy exists but is not effective at the requested time."""


class StrategyConflictError(StrategyRegistryError):
    """Multiple active strategies claim the same contract."""


class StrategyRegistryIntegrityError(StrategyRegistryError):
    """Manifest storage or artifact verification is untrustworthy."""


@dataclass(frozen=True, slots=True)
class ManifestRecord:
    """One validated manifest and its canonical identity."""

    manifest: StrategyManifest
    manifest_path: Path
    manifest_sha256: str


@dataclass(frozen=True, slots=True)
class ResolvedStrategy:
    """A verified strategy approved for a downstream consumer."""

    manifest: StrategyManifest
    manifest_path: Path
    manifest_sha256: str
    artifact_path: Path


def _canonical_symbol(symbol: str) -> str:
    if not isinstance(symbol, str):
        raise StrategyNotFoundError(
            "symbol must be a string"
        )

    canonical = symbol.strip().upper()

    if not _SYMBOL_RE.fullmatch(canonical):
        raise StrategyNotFoundError(
            "symbol must use canonical BASE/QUOTE form"
        )

    return canonical


def _canonical_horizon(horizon: str) -> str:
    if not isinstance(horizon, str):
        raise StrategyNotFoundError(
            "horizon must be a string"
        )

    canonical = horizon.strip()

    if canonical not in ALLOWED_HORIZONS:
        raise StrategyNotFoundError(
            f"horizon must be one of "
            f"{sorted(ALLOWED_HORIZONS)}"
        )

    return canonical


def _resolution_time(
    value: Optional[datetime],
) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)

    if value.tzinfo is None or value.utcoffset() is None:
        raise StrategyNotReadyError(
            "resolution time must include an explicit timezone"
        )

    return value.astimezone(timezone.utc)


def _manifest_time(value: str) -> datetime:
    candidate = (
        value[:-1] + "+00:00"
        if value.endswith("Z")
        else value
    )

    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise StrategyRegistryIntegrityError(
            "manifest contains an invalid timestamp"
        ) from exc

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise StrategyRegistryIntegrityError(
            "manifest contains a timezone-naive timestamp"
        )

    return parsed.astimezone(timezone.utc)


def _trusted_root(
    path: Path,
    label: str,
) -> Path:
    supplied = Path(path).expanduser()

    if supplied.is_symlink():
        raise StrategyRegistryIntegrityError(
            f"{label} cannot be a symbolic link"
        )

    try:
        resolved = supplied.resolve(strict=True)
    except OSError as exc:
        raise StrategyRegistryIntegrityError(
            f"{label} does not exist: {supplied}"
        ) from exc

    if not resolved.is_dir():
        raise StrategyRegistryIntegrityError(
            f"{label} must be a directory: {resolved}"
        )

    return resolved


def _reject_symlink_chain(
    root: Path,
    relative_path: Path,
    label: str,
) -> None:
    current = root

    for component in relative_path.parts:
        current = current / component

        if current.is_symlink():
            raise StrategyRegistryIntegrityError(
                f"{label} cannot contain symbolic links: "
                f"{relative_path.as_posix()}"
            )


class StrategyManifestRegistry:
    """Fail-closed inventory and resolver for promoted strategies."""

    def __init__(
        self,
        manifest_root: Path,
        artifact_root: Path,
    ) -> None:
        self.manifest_root = _trusted_root(
            manifest_root,
            "manifest_root",
        )
        self.artifact_root = _trusted_root(
            artifact_root,
            "artifact_root",
        )

    def _manifest_paths(self) -> tuple[Path, ...]:
        discovered: list[Path] = []

        for candidate in self.manifest_root.rglob("*.json"):
            relative = candidate.relative_to(
                self.manifest_root
            )

            _reject_symlink_chain(
                self.manifest_root,
                relative,
                "manifest path",
            )

            try:
                resolved = candidate.resolve(strict=True)
            except OSError as exc:
                raise StrategyRegistryIntegrityError(
                    "manifest disappeared during discovery: "
                    f"{relative.as_posix()}"
                ) from exc

            try:
                resolved.relative_to(self.manifest_root)
            except ValueError as exc:
                raise StrategyRegistryIntegrityError(
                    "manifest escapes manifest_root: "
                    f"{relative.as_posix()}"
                ) from exc

            if not resolved.is_file():
                raise StrategyRegistryIntegrityError(
                    "manifest is not a regular file: "
                    f"{relative.as_posix()}"
                )

            discovered.append(resolved)

        return tuple(
            sorted(
                discovered,
                key=lambda path: path.relative_to(
                    self.manifest_root
                ).as_posix(),
            )
        )

    def list_manifests(
        self,
    ) -> tuple[ManifestRecord, ...]:
        records: list[ManifestRecord] = []

        for manifest_path in self._manifest_paths():
            try:
                manifest = load_manifest(
                    manifest_path
                )
            except ManifestError as exc:
                relative = manifest_path.relative_to(
                    self.manifest_root
                )

                raise StrategyRegistryIntegrityError(
                    "invalid strategy manifest: "
                    f"{relative.as_posix()}"
                ) from exc

            records.append(
                ManifestRecord(
                    manifest=manifest,
                    manifest_path=manifest_path,
                    manifest_sha256=(
                        manifest.manifest_sha256()
                    ),
                )
            )

        return tuple(records)

    def resolve(
        self,
        symbol: str,
        horizon: str,
        *,
        as_of: Optional[datetime] = None,
    ) -> ResolvedStrategy:
        canonical_symbol = _canonical_symbol(symbol)
        canonical_horizon = _canonical_horizon(horizon)
        effective_at = _resolution_time(as_of)

        matching = [
            record
            for record in self.list_manifests()
            if record.manifest.symbol == canonical_symbol
            and record.manifest.horizon == canonical_horizon
        ]

        if not matching:
            raise StrategyNotFoundError(
                "no manifest exists for "
                f"{canonical_symbol} {canonical_horizon}"
            )

        active = [
            record
            for record in matching
            if record.manifest.status == "active"
        ]

        if not active:
            statuses = sorted(
                {
                    record.manifest.status
                    for record in matching
                }
            )

            raise StrategyNotFoundError(
                "no active manifest exists for "
                f"{canonical_symbol} {canonical_horizon}; "
                f"available statuses={statuses}"
            )

        eligible = [
            record
            for record in active
            if _manifest_time(
                record.manifest.promoted_at
            )
            <= effective_at
        ]

        if not eligible:
            earliest = min(
                record.manifest.promoted_at
                for record in active
            )

            raise StrategyNotReadyError(
                "active manifest for "
                f"{canonical_symbol} {canonical_horizon} "
                f"is not effective until {earliest}"
            )

        if len(eligible) > 1:
            identities = sorted(
                (
                    record.manifest.strategy_version,
                    record.manifest_sha256,
                )
                for record in eligible
            )

            raise StrategyConflictError(
                "multiple active manifests claim "
                f"{canonical_symbol} {canonical_horizon}: "
                f"{identities}"
            )

        selected = eligible[0]

        artifact_relative = Path(
            *PurePosixPath(
                selected.manifest.artifact_path
            ).parts
        )

        _reject_symlink_chain(
            self.artifact_root,
            artifact_relative,
            "artifact path",
        )

        try:
            artifact_path = (
                selected.manifest.assert_issuable(
                    self.artifact_root
                )
            )
        except ManifestError as exc:
            raise StrategyRegistryIntegrityError(
                "artifact verification failed for "
                f"{canonical_symbol} {canonical_horizon}"
            ) from exc

        # Recheck after hashing to detect an obvious symlink replacement
        # that occurred while verification was in progress.
        _reject_symlink_chain(
            self.artifact_root,
            artifact_relative,
            "artifact path",
        )

        return ResolvedStrategy(
            manifest=selected.manifest,
            manifest_path=selected.manifest_path,
            manifest_sha256=(
                selected.manifest_sha256
            ),
            artifact_path=artifact_path,
        )
