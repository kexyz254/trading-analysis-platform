"""The signed evidence path cannot expose raw journal text or write trading state."""

import sqlite3

import httpx
from fastapi.testclient import TestClient

from services.nexuss_bridge.app import create_app
from services.nexuss_bridge.protocol import signed_headers
from webapp.account_evidence import account_evidence


def _journal(path):
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE circuit_breaker_events (id INTEGER PRIMARY KEY, timestamp TEXT, tripped INT, reason TEXT);
            CREATE TABLE equity_snapshots (id INTEGER PRIMARY KEY, timestamp TEXT, total_equity REAL, is_live INT, positions TEXT);
            CREATE TABLE capital_flow_audit (id INTEGER PRIMARY KEY, timestamp TEXT, is_live INT,
                raw_delta REAL, trading_delta REAL, unexplained_delta REAL, is_external_flow INT, reason TEXT);
            CREATE TABLE trade_audit (id INTEGER PRIMARY KEY, exit_timestamp TEXT, realized_pnl REAL, exit_reason TEXT);
        """)
        db.execute("INSERT INTO circuit_breaker_events VALUES (1, ?, 1, ?)",
                   ("2026-09-07T12:02:09+00:00", "password=secret breaker reason"))
        db.executemany("INSERT INTO equity_snapshots VALUES (?, ?, ?, 1, ?)", [
            (1, "2026-09-05T21:46:58+00:00", 206.18, "secret position"),
            (2, "2026-09-07T12:03:00+00:00", 135.10, "secret position"),
        ])
        db.executemany("INSERT INTO capital_flow_audit VALUES (?, ?, 1, ?, 0, ?, ?, ?)", [
            (1, "2026-09-06T07:45:25+00:00", -23.35, -23.35, 1, "api_key=hidden"),
            (2, "2026-09-07T12:02:03+00:00", -0.48, -0.48, 0, "password=hidden"),
        ])
        db.execute("INSERT INTO trade_audit VALUES (1, ?, 0, ?)",
                   ("2026-09-04T12:00:00+00:00", "private trade note"))


def test_account_evidence_reports_misclassified_spending_without_raw_text(tmp_path):
    path = tmp_path / "trading.db"
    _journal(path)
    result = account_evidence(str(path))
    assert result["movement_count"] == 2
    assert abs(result["unclassified_sum"] + 0.48) < 1e-9
    assert abs(result["classified_external_sum"] + 23.35) < 1e-9
    assert result["trade_count"] == 0
    assert result["highest_snapshot_equity"] == 206.18
    assert result["after_incident_equity"] == 135.10
    assert result["truncated"] is False
    assert "secret" not in str(result) and "password" not in str(result)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM circuit_breaker_events").fetchone()[0] == 1


def test_signed_bridge_projects_account_evidence_and_no_unsafe_fields(tmp_path):
    secret = b"fake-bridge-secret-keep-test-only-123456"
    journal = tmp_path / "trading.db"
    _journal(journal)

    def upstream(request):
        assert request.url.path == "/api/agent/account-evidence"
        return httpx.Response(200, json={**account_evidence(str(journal)), "token": "leak"})

    client = TestClient(create_app(secret, str(tmp_path / "bridge.db"),
                                   transport=httpx.MockTransport(upstream)))
    target = "/agent/v1/evidence/account_flow"
    assert client.get(target).status_code == 401
    response = client.get(target, headers=signed_headers(secret, "GET", target))
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["movement_count"] == 2
    assert data["movements"][1]["classified_external"] is False
    assert "leak" not in response.text and "secret" not in response.text
