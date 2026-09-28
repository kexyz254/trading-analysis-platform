import sqlite3

import pytest

from webapp.price_call_results import results


NOW = 1_800_000_000_000


def _ledger(path):
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE price_calls (
            id INTEGER PRIMARY KEY, symbol TEXT, horizon_label TEXT, legacy INTEGER,
            voided INTEGER, settled_at INTEGER, correct INTEGER,
            actual_direction TEXT, created_at INTEGER, due_at INTEGER,
            direction TEXT, price_to_beat REAL, actual_price REAL,
            settlement_method TEXT, primary_benchmark_eligible INTEGER
        )""")
        rows = [
            ("BTC/USDT", "5m", 0, 0, NOW - 1000, 1, "UP", NOW - 301000,
             NOW - 1000, "UP", 100., 101., "OFFICIAL_CLOSE", 1),
            ("BTC/USDT", "5m", 0, 0, NOW - 2000, 0, "DOWN", NOW - 302000,
             NOW - 2000, "UP", 100., 99., "OFFICIAL_CLOSE", 1),
            ("BTC/USDT", "5m", 0, 0, NOW - 3000, 0, "FLAT", NOW - 303000,
             NOW - 3000, "UP", 100., 100., "OFFICIAL_CLOSE", 0),
            ("BTC/USDT", "5m", 0, 1, None, None, None, NOW - 100,
             NOW + 299900, "UP", 100., None, None, None),
            ("BTC/USDT", "5m", 1, 0, NOW - 1000, 1, "UP", NOW - 301000,
             NOW - 1000, "UP", 100., 101., "LEGACY", 0),
            ("BTC/USDT", "15m", 0, 0, None, None, None, NOW - 100,
             NOW + 899900, "UP", 100., None, None, None),
            ("ETH/USDT", "5m", 0, 0, NOW - 1000, 1, "UP", NOW - 301000,
             NOW - 1000, "UP", 100., 101., "OFFICIAL_CLOSE", 1),
        ]
        db.executemany("""INSERT INTO price_calls (
          symbol,horizon_label,legacy,voided,settled_at,correct,actual_direction,
          created_at,due_at,direction,price_to_beat,actual_price,
          settlement_method,primary_benchmark_eligible
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)


def test_tracker_reads_only_btc_paper_calls_and_respects_denominators(tmp_path):
    path = tmp_path / "analytics.db"
    _ledger(path)
    with sqlite3.connect(path) as db:
        before = db.execute("SELECT COUNT(*) FROM price_calls").fetchone()[0]
    data = results(str(path), now_ms=NOW)
    assert data["scope"] == "paper_price_calls_no_exchange_orders"
    five = data["horizons"]["5m"]
    assert (five["recorded"], five["voided"], five["open"]) == (4, 1, 0)
    assert five["last_24h"]["resolved"] == 3
    assert five["last_24h"]["directional"] == 2
    assert five["last_24h"]["hits"] == 1
    assert five["last_24h"]["hit_rate"] == .5
    assert five["last_24h"]["sample_sufficient"] is False
    assert len(five["recent"]) == 3
    assert data["horizons"]["15m"]["open"] == 1
    assert data["horizons"]["15m"]["last_24h"]["hit_rate"] is None
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM price_calls").fetchone()[0] == before


def test_missing_db_is_explicit_and_schema_mismatch_fails_closed(tmp_path):
    path = tmp_path / "absent.db"
    assert results(str(path), now_ms=NOW)["status"] == "no_analytics_db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE price_calls(id INTEGER PRIMARY KEY)")
    with pytest.raises(ValueError, match="schema unavailable"):
        results(str(path), now_ms=NOW)
