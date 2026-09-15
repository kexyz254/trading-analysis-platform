"""Production storage health and capacity classification for UMIL."""

from __future__ import annotations

import shutil
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path


class StorageState(str, Enum):
    NORMAL = "NORMAL"
    WATCH = "WATCH"
    WARNING = "WARNING"
    ARCHIVE_PRESSURE = "ARCHIVE_PRESSURE"
    CRITICAL = "CRITICAL"
    EMERGENCY = "EMERGENCY"


@dataclass(frozen=True, slots=True)
class StorageHealth:
    disk_total_bytes: int
    disk_used_bytes: int
    disk_free_bytes: int
    disk_used_pct: float
    umil_db_bytes: int
    object_store_bytes: int
    object_count: int
    state: StorageState

    def as_dict(self) -> dict:
        data = asdict(self)
        data["state"] = self.state.value
        return data


def _tree_size(path: Path) -> tuple[int, int]:
    if not path.exists():
        return 0, 0

    total = 0
    count = 0

    for item in path.rglob("*"):
        if item.is_file():
            try:
                total += item.stat().st_size
                count += 1
            except FileNotFoundError:
                # Safe during concurrent content-addressed writes.
                continue

    return total, count


def classify_storage(
    *,
    used_pct: float,
    free_bytes: int,
) -> StorageState:
    gib = 1024 ** 3

    # Absolute free-space protection takes precedence.
    if free_bytes < 3 * gib or used_pct >= 93:
        return StorageState.EMERGENCY

    if free_bytes < 6 * gib or used_pct >= 88:
        return StorageState.CRITICAL

    if used_pct >= 82:
        return StorageState.ARCHIVE_PRESSURE

    if free_bytes < 10 * gib or used_pct >= 75:
        return StorageState.WARNING

    if used_pct >= 65:
        return StorageState.WATCH

    return StorageState.NORMAL


def inspect_storage(
    *,
    database_path: Path,
) -> StorageHealth:
    database_path = database_path.expanduser().resolve()
    data_root = database_path.parent
    object_root = data_root / "objects"

    usage = shutil.disk_usage(data_root)

    used_pct = (
        usage.used / usage.total * 100.0
        if usage.total
        else 100.0
    )

    object_bytes, object_count = _tree_size(object_root)

    db_bytes = (
        database_path.stat().st_size
        if database_path.exists()
        else 0
    )

    state = classify_storage(
        used_pct=used_pct,
        free_bytes=usage.free,
    )

    return StorageHealth(
        disk_total_bytes=usage.total,
        disk_used_bytes=usage.used,
        disk_free_bytes=usage.free,
        disk_used_pct=round(used_pct, 2),
        umil_db_bytes=db_bytes,
        object_store_bytes=object_bytes,
        object_count=object_count,
        state=state,
    )
