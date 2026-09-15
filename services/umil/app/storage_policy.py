"""UMIL storage policy, pressure actions, and dry-run orphan discovery."""

from __future__ import annotations

import gzip
import json
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from services.umil.app.storage_health import StorageState


class DataClass(str, Enum):
    PROTECTED = "PROTECTED"
    LONG_TERM = "LONG_TERM"
    RAW_REPLAY = "RAW_REPLAY"
    EPHEMERAL = "EPHEMERAL"


PROTECTED_EVENT_TYPES = frozenset({
    "CIRCUIT_BREAKER_EVENT",
    "TRADE_AUDIT",
    "TRADE_EXECUTION",
    "ORDER_EVENT",
    "FILL_EVENT",
    "BALANCE_EVENT",
    "RISK_EVENT",
    "MODEL_PROMOTION",
    "MODEL_REJECTION",
    "GOVERNANCE_DECISION",
})

LONG_TERM_EVENT_TYPES = frozenset({
    "FEATURE_OBSERVATION",
    "MARKET_INTELLIGENCE_SNAPSHOT",
    "MICROSTRUCTURE_OBSERVATION",
    "WATCH_CONDITION_CREATED",
    "WATCH_CONDITION_TRANSITION",
    "ASSET_SCALE_SNAPSHOT",
})

RAW_REPLAY_EVENT_TYPES = frozenset({
    "MARKET_INPUT_BATCH",
})

EPHEMERAL_EVENT_TYPES = frozenset({
    "TEMPORARY_DIAGNOSTIC",
    "CACHE_RECORD",
})


def classify_event(event_type: str) -> DataClass:
    if event_type in PROTECTED_EVENT_TYPES:
        return DataClass.PROTECTED

    if event_type in LONG_TERM_EVENT_TYPES:
        return DataClass.LONG_TERM

    if event_type in RAW_REPLAY_EVENT_TYPES:
        return DataClass.RAW_REPLAY

    if event_type in EPHEMERAL_EVENT_TYPES:
        return DataClass.EPHEMERAL

    # Unknown evidence defaults to conservative retention.
    return DataClass.LONG_TERM


@dataclass(frozen=True, slots=True)
class StorageAction:
    allow_gc: bool
    allow_archive: bool
    allow_retention_expiry: bool
    throttle_noncritical_raw: bool
    protected_delete_allowed: bool


def actions_for_state(state: StorageState) -> StorageAction:
    if state in (StorageState.NORMAL, StorageState.WATCH):
        return StorageAction(
            allow_gc=True,
            allow_archive=False,
            allow_retention_expiry=True,
            throttle_noncritical_raw=False,
            protected_delete_allowed=False,
        )

    if state == StorageState.WARNING:
        return StorageAction(
            allow_gc=True,
            allow_archive=True,
            allow_retention_expiry=True,
            throttle_noncritical_raw=False,
            protected_delete_allowed=False,
        )

    if state == StorageState.ARCHIVE_PRESSURE:
        return StorageAction(
            allow_gc=True,
            allow_archive=True,
            allow_retention_expiry=True,
            throttle_noncritical_raw=False,
            protected_delete_allowed=False,
        )

    return StorageAction(
        allow_gc=True,
        allow_archive=True,
        allow_retention_expiry=True,
        throttle_noncritical_raw=True,
        protected_delete_allowed=False,
    )


def referenced_object_paths(database_rows) -> set[str]:
    """Return cold-object relative paths referenced by ledger manifests."""
    referenced: set[str] = set()

    for payload_json, in database_rows:
        try:
            payload = json.loads(payload_json)
        except (TypeError, json.JSONDecodeError):
            continue

        if not isinstance(payload, dict):
            continue

        descriptor = payload.get("__umil_external_payload__")

        if not isinstance(descriptor, dict):
            continue

        path = descriptor.get("relative_path")

        if isinstance(path, str):
            referenced.add(path)

    return referenced


@dataclass(frozen=True, slots=True)
class OrphanCandidate:
    relative_path: str
    size_bytes: int
    age_seconds: float


def find_orphan_candidates(
    *,
    data_root: Path,
    referenced_paths: set[str],
    grace_seconds: int = 24 * 60 * 60,
    now: float | None = None,
) -> list[OrphanCandidate]:
    """Find unreferenced cold objects without deleting anything."""
    root = data_root.resolve()
    object_root = (root / "objects").resolve()

    current = time.time() if now is None else now
    results: list[OrphanCandidate] = []

    if not object_root.exists():
        return results

    for path in object_root.rglob("*.json.gz"):
        if not path.is_file():
            continue

        relative = str(path.resolve().relative_to(root))

        if relative in referenced_paths:
            continue

        stat = path.stat()
        age = max(0.0, current - stat.st_mtime)

        if age < grace_seconds:
            continue

        # Candidate validation: readable gzip only.
        try:
            with gzip.open(path, "rb") as handle:
                handle.read(1)
        except OSError:
            # Corruption requires separate incident handling.
            continue

        results.append(
            OrphanCandidate(
                relative_path=relative,
                size_bytes=stat.st_size,
                age_seconds=age,
            )
        )

    return sorted(
        results,
        key=lambda item: item.relative_path,
    )
