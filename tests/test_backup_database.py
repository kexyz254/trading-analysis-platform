import sqlite3
import time

import pytest

from scripts.backup_database import backup_database


def _make_db(path, value="hello"):
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE t (v TEXT)")
    conn.execute("INSERT INTO t VALUES (?)", (value,))
    conn.commit()
    conn.close()


def test_backup_creates_a_consistent_copy_with_the_data_intact(tmp_path):
    db_path = tmp_path / "trading_system.db"
    _make_db(db_path)

    dest = backup_database(str(db_path), str(tmp_path / "backups"), keep=14)

    conn = sqlite3.connect(str(dest))
    rows = conn.execute("SELECT v FROM t").fetchall()
    conn.close()
    assert rows == [("hello",)]


def test_backup_raises_clearly_when_source_is_missing(tmp_path):
    with pytest.raises(FileNotFoundError, match="no database found"):
        backup_database(str(tmp_path / "does_not_exist.db"), str(tmp_path / "backups"), keep=14)


def test_backup_rotation_keeps_only_the_most_recent_n(tmp_path):
    db_path = tmp_path / "trading_system.db"
    _make_db(db_path)
    backup_dir = tmp_path / "backups"

    for _ in range(5):
        backup_database(str(db_path), str(backup_dir), keep=3)
        time.sleep(1.1)  # backups are named by second-resolution timestamp - must not collide

    remaining = sorted(backup_dir.glob("trading_system-*.db"))
    assert len(remaining) == 3


def test_keep_zero_removes_all_previous_backups_each_run(tmp_path):
    db_path = tmp_path / "trading_system.db"
    _make_db(db_path)
    backup_dir = tmp_path / "backups"

    backup_database(str(db_path), str(backup_dir), keep=0)

    remaining = sorted(backup_dir.glob("trading_system-*.db"))
    assert remaining == []


def test_backup_does_not_mutate_the_source_database(tmp_path):
    db_path = tmp_path / "trading_system.db"
    _make_db(db_path)

    backup_database(str(db_path), str(tmp_path / "backups"), keep=14)

    conn = sqlite3.connect(str(db_path))
    rows = conn.execute("SELECT v FROM t").fetchall()
    conn.close()
    assert rows == [("hello",)]
