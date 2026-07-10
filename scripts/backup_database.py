"""Online backup of trading_system.db, safe to run while the engine and
dashboard are both live.

Uses sqlite3's own backup API (Connection.backup), not a raw file copy -
a raw copy taken while WAL mode is active can catch the main db file
mid-checkpoint, without the matching -wal contents, producing a corrupt
snapshot. The backup API takes a consistent, point-in-time copy through
SQLite's own locking, which is what "online backup" is specifically
designed for.

Deliberately a standalone script, meant to be invoked on a schedule (cron
on Linux, Task Scheduler on Windows) rather than run inside the live
engine process - backup/retention policy is an operations concern, kept
separate from the trading loop the same way scripts/emergency_flatten.py
keeps the kill switch separate.

Usage:
    python scripts/backup_database.py
    python scripts/backup_database.py --db-path trading_system.db --backup-dir backups --keep 14

Suggested cron entry (daily at 02:00, keep 14 days):
    0 2 * * * cd /path/to/repo && PYTHONPATH=. python3 scripts/backup_database.py

For an off-server copy (recommended - a backup that lives on the same disk
as what it's backing up doesn't protect against disk failure), sync the
backup directory to remote storage (rclone, rsync, S3, etc.) as a separate
step after this script runs; that's a deployment-specific choice this
script deliberately doesn't make for you.
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def backup_database(db_path: str, backup_dir: str, keep: int) -> Path:
    """Take a consistent online backup of db_path into backup_dir, then
    delete backups beyond the most recent `keep` (oldest first) - this
    IS deletion, but deliberately: these are redundant copies of a live
    database that's itself continuously backed up on the next run, not an
    audit record - unlike trading_system.db's own trade_audit table, there
    is no "never delete" principle that applies to rotating backups."""
    source = Path(db_path)
    if not source.exists():
        raise FileNotFoundError(f"no database found at {db_path!r} - nothing to back up")

    dest_dir = Path(backup_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest_path = dest_dir / f"{source.stem}-{timestamp}.db"

    source_conn = sqlite3.connect(str(source))
    try:
        dest_conn = sqlite3.connect(str(dest_path))
        try:
            source_conn.backup(dest_conn)
        finally:
            dest_conn.close()
    finally:
        source_conn.close()

    logger.info(f"backed up {source} -> {dest_path} ({dest_path.stat().st_size} bytes)")

    existing = sorted(dest_dir.glob(f"{source.stem}-*.db"), key=lambda p: p.name)
    to_remove = existing[:-keep] if keep > 0 else existing
    for old_backup in to_remove:
        old_backup.unlink()
        logger.info(f"removed old backup beyond retention: {old_backup}")

    return dest_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Online backup of trading_system.db")
    parser.add_argument("--db-path", default="trading_system.db")
    parser.add_argument("--backup-dir", default="backups")
    parser.add_argument("--keep", type=int, default=14, help="How many recent backups to retain (0 = keep all)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        backup_database(args.db_path, args.backup_dir, args.keep)
    except FileNotFoundError as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
