"""Runtime paths for persistent trading state.

The production deployment keeps SQLite databases inside bind-mounted
DIRECTORIES rather than bind-mounting a database file by itself. SQLite WAL
mode creates ``-wal`` and ``-shm`` sidecars next to the main database; keeping
the whole directory shared is required for crash-safe cross-process reads and
schema migrations.
"""

from __future__ import annotations

import os
from pathlib import Path

TRADING_DB_PATH_ENV = "ATS_TRADING_DB_PATH"
DEFAULT_TRADING_DB_PATH = "trading_system.db"
MIGRATED_TRADING_DB_PATH = "trading_data/trading_system.db"


def trading_db_path(default: str = DEFAULT_TRADING_DB_PATH) -> str:
    """Return the configured trading journal path.

    Local development remains backward compatible with ``trading_system.db``
    in the current working directory. Production sets ``ATS_TRADING_DB_PATH``
    to ``/app/trading_data/trading_system.db`` in Docker Compose.
    """

    configured = os.environ.get(TRADING_DB_PATH_ENV)
    if configured is not None:
        value = configured.strip()
        if not value:
            raise ValueError(f"{TRADING_DB_PATH_ENV} must not be empty")
        return value

    # Production host-side maintenance scripts run outside Docker and do not
    # inherit Compose's environment block. Once storage v2 exists, prefer it
    # automatically so a reset/backup/correction command cannot silently hit
    # the retained legacy single-file database. Fresh local development still
    # falls back to the historical path for backward compatibility.
    if Path(MIGRATED_TRADING_DB_PATH).exists():
        return MIGRATED_TRADING_DB_PATH
    return default
