from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import yaml

from trading_assistant.persistence.journal import Journal
from trading_assistant.persistence.paths import trading_db_path


def test_trading_db_path_uses_environment(monkeypatch, tmp_path):
    configured = tmp_path / "state" / "trading_system.db"
    monkeypatch.setenv("ATS_TRADING_DB_PATH", str(configured))
    assert trading_db_path() == str(configured)


def test_trading_db_path_default_is_backward_compatible(monkeypatch):
    monkeypatch.delenv("ATS_TRADING_DB_PATH", raising=False)
    assert trading_db_path() == "trading_system.db"




def test_trading_db_path_prefers_migrated_host_layout(monkeypatch, tmp_path):
    monkeypatch.delenv("ATS_TRADING_DB_PATH", raising=False)
    monkeypatch.chdir(tmp_path)
    migrated = tmp_path / "trading_data" / "trading_system.db"
    migrated.parent.mkdir()
    migrated.touch()
    assert trading_db_path() == "trading_data/trading_system.db"


def test_reader_connection_is_enforced_read_only(tmp_path):
    db_path = tmp_path / "journal.db"
    writer = Journal(str(db_path))
    writer.record_decision(symbol="BTC/USDT", risk_approved=True)

    reader = Journal(str(db_path), initialize=False)
    with reader._connect() as connection:
        assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            connection.execute(
                "INSERT INTO decisions "
                "(timestamp, symbol, risk_approved) VALUES ('x', 'x', 1)"
            )


def test_compose_uses_shared_directory_for_engine_and_readonly_dashboard():
    compose = yaml.safe_load(Path("docker-compose.yml").read_text())
    services = compose["services"]

    engine = services["engine"]
    dashboard = services["dashboard"]

    assert "./trading_data:/app/trading_data" in engine["volumes"]
    assert "./trading_data:/app/trading_data:ro" in dashboard["volumes"]

    assert all(
        not str(volume).startswith("./trading_system.db:")
        for service in (engine, dashboard)
        for volume in service["volumes"]
    )

    expected = "/app/trading_data/trading_system.db"
    assert engine["environment"]["ATS_TRADING_DB_PATH"] == expected
    assert dashboard["environment"]["ATS_TRADING_DB_PATH"] == expected


def test_engine_lock_path_follows_configured_database(monkeypatch, tmp_path):
    from run_system import _engine_lock_path

    database = tmp_path / "state" / "trading_system.db"
    monkeypatch.setenv("ATS_TRADING_DB_PATH", str(database))

    assert _engine_lock_path() == database.parent.resolve() / "run_system.lock"
