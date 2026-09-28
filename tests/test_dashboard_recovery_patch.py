from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

from trading_assistant.persistence.journal import Journal
from webapp.data_access import TTLCache, database_readiness


def test_ttl_cache_is_bounded_and_lru():
    cache = TTLCache(max_entries=2)
    assert cache.get_or_set("a", 60, lambda: 1) == 1
    assert cache.get_or_set("b", 60, lambda: 2) == 2
    assert cache.get_or_set("a", 60, lambda: 99) == 1  # refresh LRU order
    assert cache.get_or_set("c", 60, lambda: 3) == 3

    assert list(cache._store) == ["a", "c"]
    assert cache.stats() == {"entries": 2, "max_entries": 2}


def test_ttl_cache_removes_expired_entries():
    cache = TTLCache(max_entries=4)
    assert cache.get_or_set("expired", 0, lambda: "old") == "old"
    assert cache.get_or_set("expired", 60, lambda: "new") == "new"
    assert cache.stats()["entries"] == 1


def test_latest_paper_equity_returns_only_latest_candidate_rows(tmp_path: Path):
    db_path = tmp_path / "journal.db"
    journal = Journal(str(db_path))

    for index in range(50):
        journal.record_paper_equity_snapshot(
            symbol="BTC/USDT",
            candidate="alpha",
            capital=1000.0,
            total_equity=1000.0 + index,
            num_trades=index,
        )
    journal.record_paper_equity_snapshot(
        symbol="BTC/USDT",
        candidate="beta",
        capital=1000.0,
        total_equity=999.0,
        num_trades=2,
    )

    latest = Journal(str(db_path), initialize=False).latest_paper_equity_by_candidate(
        "BTC/USDT"
    )

    assert set(latest) == {"alpha", "beta"}
    assert latest["alpha"]["total_equity"] == 1049.0
    assert latest["alpha"]["num_trades"] == 49


def test_database_readiness_is_read_only_and_reports_missing_database(tmp_path: Path):
    missing = tmp_path / "missing.db"
    result = database_readiness(str(missing))
    assert result["ok"] is False
    assert not missing.exists()


def test_database_readiness_checks_live_schema(tmp_path: Path):
    db_path = tmp_path / "journal.db"
    Journal(str(db_path))
    result = database_readiness(str(db_path))
    assert result["ok"] is True
    assert result["schema_visible"] is True


def test_dashboard_import_does_not_import_autonomous_engine():
    code = """
import sys
from unittest.mock import patch
from webapp.operator_auth import OperatorAuth
with patch.object(OperatorAuth, 'from_dashboard_data', return_value=OperatorAuth('test-hash', 'test-session-secret')):
    import webapp.server
assert 'run_system' not in sys.modules
assert 'trading_assistant.agents.strategy_manager' not in sys.modules
print('ok')
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stdout.strip() == "ok"
