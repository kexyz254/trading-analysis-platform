import sqlite3
import tempfile
import unittest
from contextlib import closing
from decimal import Decimal
from pathlib import Path

from webapp.market_state import (
    MARKET_STATE_VERSION,
    ClosedCandle,
    MarketStateEngine,
)
from webapp.price_call import RealTimePriceCallEngine
from webapp.quant_tournament import CANDIDATES, QuantStrategyTournament, QuantTournamentBook


INTERVAL = 300_000
SYMBOL = "BTC/USDT"


def candle(index: int, *, cycle_start: int, close_shift: float = 0.0) -> ClosedCandle:
    open_time = cycle_start - (40 - index) * INTERVAL
    base = 100.0 + index * 0.04 + close_shift
    return ClosedCandle(
        symbol=SYMBOL,
        horizon="5m",
        open_time_ms=open_time,
        close_time_ms=open_time + INTERVAL - 1,
        open_text=f"{base:.8f}",
        high_text=f"{base + 0.30:.8f}",
        low_text=f"{base - 0.30:.8f}",
        close_text=f"{base + 0.05:.8f}",
        volume_text="10.0",
    )


class MarketStateTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self._tmp.name) / "analytics.db")
        self.engine = MarketStateEngine(self.db_path)
        self.cycle_start = 12_000_000
        self.decision_at = self.cycle_start + 100

    def tearDown(self):
        self._tmp.cleanup()

    def warm(self, count: int = 40, *, observed_at: int = None) -> int:
        return self.engine.ingest_closed_candles(
            [candle(index, cycle_start=self.cycle_start) for index in range(count)],
            observed_at_ms=observed_at or self.cycle_start + 1,
            source="TEST_BINANCE_REST_WARMUP",
            evidence_role="WARMUP",
        )

    def test_closed_candle_warmup_is_ready_without_creating_trading_evidence(self):
        self.assertEqual(self.warm(), 40)
        state = self.engine.assess_cycle(
            symbol=SYMBOL,
            horizon="5m",
            cycle_start_ms=self.cycle_start,
            decision_at_ms=self.decision_at,
        )
        self.assertEqual(state.data_state, "READY")
        self.assertNotEqual(state.regime, "unclassified")
        self.assertEqual(state.missing_intervals, 0)
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            tables = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        self.assertNotIn("quant_candidate_decisions", tables)
        self.assertNotIn("quant_candidate_outcomes", tables)

    def test_point_in_time_assessment_excludes_late_observations(self):
        self.warm(39, observed_at=self.cycle_start - INTERVAL + 10)
        latest = candle(39, cycle_start=self.cycle_start)
        self.engine.ingest_closed_candle(
            latest,
            observed_at_ms=self.decision_at + 1_000,
            source="LATE_TEST_OBSERVATION",
            evidence_role="WARMUP",
        )
        state = self.engine.assess_cycle(
            symbol=SYMBOL,
            horizon="5m",
            cycle_start_ms=self.cycle_start,
            decision_at_ms=self.decision_at,
        )
        self.assertEqual(state.candle_count, 39)
        self.assertEqual(state.data_state, "STALE")
        self.assertEqual(state.latest_close_age_intervals, 1)

    def test_gap_and_conflicting_cycle_fail_closed(self):
        candles = [candle(index, cycle_start=self.cycle_start) for index in range(40)]
        del candles[20]
        self.engine.ingest_closed_candles(
            candles,
            observed_at_ms=self.cycle_start + 1,
            source="TEST_GAPPED_WARMUP",
        )
        state = self.engine.assess_cycle(
            symbol=SYMBOL,
            horizon="5m",
            cycle_start_ms=self.cycle_start,
            decision_at_ms=self.decision_at,
        )
        self.assertEqual(state.data_state, "DATA_GAP")
        self.assertEqual(state.regime, "unclassified")
        self.assertGreater(state.missing_intervals, 0)
        existing = candle(39, cycle_start=self.cycle_start)
        equivalent = ClosedCandle(
            symbol=existing.symbol,
            horizon=existing.horizon,
            open_time_ms=existing.open_time_ms,
            close_time_ms=existing.close_time_ms,
            open_text=str(float(existing.open_text)),
            high_text=str(float(existing.high_text)),
            low_text=str(float(existing.low_text)),
            close_text=str(float(existing.close_text)),
            volume_text="10.000000",
        )
        self.assertFalse(self.engine.ingest_closed_candle(
            equivalent,
            observed_at_ms=self.cycle_start + 2,
            source="EQUIVALENT_FORMAT",
            evidence_role="WARMUP",
        ))
        with self.assertRaises(sqlite3.IntegrityError):
            self.engine.ingest_closed_candle(
                candle(39, cycle_start=self.cycle_start, close_shift=4.0),
                observed_at_ms=self.cycle_start + 2,
                source="CONFLICT",
                evidence_role="WARMUP",
            )

    def test_cycle_state_is_immutable_and_replay_idempotent(self):
        self.warm()
        state = self.engine.assess_cycle(
            symbol=SYMBOL,
            horizon="5m",
            cycle_start_ms=self.cycle_start,
            decision_at_ms=self.decision_at,
        )
        first = self.engine.record_assessment(state)
        second = self.engine.record_assessment(state)
        self.assertEqual(first, second)
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "UPDATE quant_market_state_snapshots SET regime='ranging' WHERE id=?",
                    (first,),
                )

    def test_every_new_candidate_decision_links_to_one_market_state(self):
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute("CREATE TABLE price_calls (id INTEGER PRIMARY KEY)")
            conn.execute("INSERT INTO price_calls(id) VALUES (1)")
        tournament = QuantStrategyTournament(
            self.db_path,
            fee_rate=Decimal("0.001"),
            slippage_rate=Decimal("0.0002"),
            clock_ms=lambda: self.decision_at,
        )
        self.warm()
        inserted = tournament.record_cycle_decisions(
            price_call_id=1,
            symbol=SYMBOL,
            horizon="5m",
            cycle_start_ms=self.cycle_start,
            due_at_ms=self.cycle_start + INTERVAL,
            decision_at_ms=self.decision_at,
            entry_price_text="101.60000000",
            orderbook_depth={
                "bids": [["101.59", "60"]],
                "asks": [["101.61", "40"]],
            },
            recent_ticks=[
                {"price": 101.5 + index * 0.01, "trade_ms": self.decision_at + index}
                for index in range(10)
            ],
            base_probability_up=0.62,
        )
        self.assertEqual(inserted, len(CANDIDATES))
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            linked, snapshots, versions = conn.execute(
                """
                SELECT COUNT(market_state_snapshot_id),
                       COUNT(DISTINCT market_state_snapshot_id),
                       COUNT(DISTINCT market_state_version)
                FROM quant_candidate_decisions
                """
            ).fetchone()
        self.assertEqual(linked, len(CANDIDATES))
        self.assertEqual(snapshots, 1)
        self.assertEqual(versions, 1)
        report = QuantTournamentBook(self.db_path).report(SYMBOL)
        health = report["horizons"]["5m"]["health"]
        self.assertEqual(health["missing_market_state_links"], 0)
        self.assertEqual(health["broken_market_state_links"], 0)
        self.assertEqual(
            report["market_state"]["horizons"]["5m"]["market_state_version"],
            MARKET_STATE_VERSION,
        )

    def test_restart_reconciliation_finishes_from_official_candle(self):
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute("CREATE TABLE price_calls (id INTEGER PRIMARY KEY)")
            conn.execute("INSERT INTO price_calls(id) VALUES (1)")
        tournament = QuantStrategyTournament(self.db_path)
        tournament.record_cycle_decisions(
            price_call_id=1,
            symbol=SYMBOL,
            horizon="5m",
            cycle_start_ms=self.cycle_start,
            due_at_ms=self.cycle_start + INTERVAL,
            decision_at_ms=self.decision_at,
            entry_price_text="100",
            orderbook_depth={"bids": [], "asks": []},
            recent_ticks=[{"price": 100, "trade_ms": self.decision_at}],
            base_probability_up=0.5,
        )
        tournament.market_state.ingest_closed_candle(
            ClosedCandle(
                symbol=SYMBOL,
                horizon="5m",
                open_time_ms=self.cycle_start,
                close_time_ms=self.cycle_start + INTERVAL - 1,
                open_text="100",
                high_text="101",
                low_text="99",
                close_text="100.5",
            ),
            observed_at_ms=self.cycle_start + INTERVAL + 25,
            source="STREAM_KLINE",
            evidence_role="PROSPECTIVE",
        )
        self.assertEqual(tournament.reconcile_pending_outcomes(), len(CANDIDATES))
        self.assertEqual(tournament.reconcile_pending_outcomes(), 0)

    def test_v111_decision_rows_are_preserved_during_additive_migration(self):
        legacy_db = str(Path(self._tmp.name) / "legacy-v111.db")
        with closing(sqlite3.connect(legacy_db)) as conn, conn:
            conn.execute("CREATE TABLE price_calls (id INTEGER PRIMARY KEY)")
            conn.execute("INSERT INTO price_calls(id) VALUES (7)")
            conn.execute(
                """
                CREATE TABLE quant_candidate_decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    price_call_id INTEGER,
                    symbol TEXT NOT NULL,
                    horizon_label TEXT NOT NULL,
                    cycle_start_ms INTEGER NOT NULL,
                    due_at_ms INTEGER NOT NULL,
                    decision_at_ms INTEGER NOT NULL,
                    candidate_name TEXT NOT NULL,
                    candidate_version TEXT NOT NULL,
                    tournament_version TEXT NOT NULL,
                    regime TEXT NOT NULL,
                    action TEXT NOT NULL,
                    entry_price_text TEXT NOT NULL,
                    probability_up REAL NOT NULL,
                    confidence REAL NOT NULL,
                    expected_move_bps REAL NOT NULL,
                    decision_reason TEXT NOT NULL,
                    feature_snapshot TEXT NOT NULL,
                    fee_rate_text TEXT NOT NULL,
                    slippage_rate_text TEXT NOT NULL,
                    UNIQUE(symbol, horizon_label, due_at_ms, candidate_name)
                )
                """
            )
            conn.execute(
                """
                INSERT INTO quant_candidate_decisions (
                    price_call_id, symbol, horizon_label, cycle_start_ms,
                    due_at_ms, decision_at_ms, candidate_name,
                    candidate_version, tournament_version, regime, action,
                    entry_price_text, probability_up, confidence,
                    expected_move_bps, decision_reason, feature_snapshot,
                    fee_rate_text, slippage_rate_text
                ) VALUES (7, 'BTC/USDT', '5m', 300000, 600000, 300010,
                          'orderbook_imbalance', '1.0.0',
                          'quant-tournament-v1.1.1', 'unclassified', 'UP',
                          '100.00000000', 0.7, 0.4, 40.0,
                          'preserved confidential reason', '{"legacy":true}',
                          '0.001', '0.0002')
                """
            )
        QuantStrategyTournament(legacy_db)
        with closing(sqlite3.connect(legacy_db)) as conn, conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM quant_candidate_decisions"
            ).fetchall()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["tournament_version"], "quant-tournament-v1.1.1")
        self.assertEqual(row["decision_reason"], "preserved confidential reason")
        self.assertEqual(row["feature_snapshot"], '{"legacy":true}')
        self.assertIsNone(row["market_state_snapshot_id"])
        self.assertIsNone(row["market_state_version"])

    def test_official_price_and_cycle_match_across_every_research_ledger(self):
        cross_db = str(Path(self._tmp.name) / "cross-engine.db")
        engine = RealTimePriceCallEngine(cross_db)
        trade_ms = 1_784_653_531_075
        call_id = engine.cast_aligned_calls(
            SYMBOL,
            "100.00000000",
            trade_ms + 1,
            trade_ms,
            {"bids": [["99.99", "90"]], "asks": [["100.01", "10"]]},
            [
                {"price": 99.9 + index * 0.01, "trade_ms": trade_ms + index}
                for index in range(10)
            ],
            ("5m",),
        )[0]
        with closing(sqlite3.connect(cross_db)) as conn, conn:
            due_at = conn.execute(
                "SELECT due_at FROM price_calls WHERE id = ?", (call_id,)
            ).fetchone()[0]
        engine.settle_closed_candle(
            SYMBOL,
            "5m",
            due_at - INTERVAL,
            due_at - 1,
            "101.00000000",
            due_at + 25,
            open_price_text="100.00000000",
            high_price_text="101.20000000",
            low_price_text="99.80000000",
        )
        with closing(sqlite3.connect(cross_db)) as conn, conn:
            conn.row_factory = sqlite3.Row
            price_close = conn.execute(
                "SELECT actual_price_text FROM price_calls WHERE id = ?",
                (call_id,),
            ).fetchone()[0]
            tournament_close = conn.execute(
                "SELECT close_text FROM quant_market_candles WHERE due_at_ms = ?",
                (due_at,),
            ).fetchone()[0]
            state_close = conn.execute(
                "SELECT close_text FROM quant_market_state_candles WHERE close_time_ms = ?",
                (due_at - 1,),
            ).fetchone()[0]
            outcomes = conn.execute(
                "SELECT close_text FROM quant_candidate_outcomes"
            ).fetchall()
            linked_snapshots = conn.execute(
                "SELECT COUNT(DISTINCT market_state_snapshot_id) FROM quant_candidate_decisions"
            ).fetchone()[0]
        self.assertEqual(price_close, "101.00000000")
        self.assertEqual(tournament_close, "101.00000000")
        self.assertEqual(state_close, "101.00000000")
        self.assertEqual(len(outcomes), len(CANDIDATES))
        self.assertEqual({row["close_text"] for row in outcomes}, {"101.00000000"})
        self.assertEqual(linked_snapshots, 1)

    def test_transactional_outbox_recovers_decisions_and_scrubs_live_inputs(self):
        outbox_db = str(Path(self._tmp.name) / "outbox.db")
        engine = RealTimePriceCallEngine(outbox_db, clock_ms=lambda: 9_000_000)
        original = engine.quant_tournament.record_cycle_decisions

        def interrupted(**_kwargs):
            raise RuntimeError("simulated interruption")

        engine.quant_tournament.record_cycle_decisions = interrupted
        call_id = engine.cast_aligned_calls(
            SYMBOL,
            "100.00000000",
            9_000_001,
            9_000_000,
            {"bids": [["99.99", "60"]], "asks": [["100.01", "40"]]},
            [{"price": 100.0, "trade_ms": 9_000_000}],
            ("5m",),
        )[0]
        with closing(sqlite3.connect(outbox_db)) as conn, conn:
            pending = conn.execute(
                "SELECT status, orderbook_json, recent_ticks_json FROM quant_decision_outbox WHERE call_id = ?",
                (call_id,),
            ).fetchone()
            decision_count = conn.execute(
                "SELECT COUNT(*) FROM quant_candidate_decisions"
            ).fetchone()[0]
        self.assertEqual(pending[0], "PENDING")
        self.assertIsNotNone(pending[1])
        self.assertIsNotNone(pending[2])
        self.assertEqual(decision_count, 0)
        pending_health = QuantTournamentBook(outbox_db).report(SYMBOL)[
            "horizons"
        ]["5m"]["health"]
        self.assertEqual(pending_health["state"], "DEGRADED")
        self.assertEqual(pending_health["pending_decision_outbox"], 1)

        engine.quant_tournament.record_cycle_decisions = original
        self.assertEqual(engine._drain_tournament_outbox(), 1)
        self.assertEqual(engine._drain_tournament_outbox(), 0)
        with closing(sqlite3.connect(outbox_db)) as conn, conn:
            completed = conn.execute(
                "SELECT status, orderbook_json, recent_ticks_json, last_error_code FROM quant_decision_outbox WHERE call_id = ?",
                (call_id,),
            ).fetchone()
            decision_count = conn.execute(
                "SELECT COUNT(*) FROM quant_candidate_decisions"
            ).fetchone()[0]
        self.assertEqual(completed[0], "COMPLETED")
        self.assertIsNone(completed[1])
        self.assertIsNone(completed[2])
        self.assertIsNone(completed[3])
        self.assertEqual(decision_count, len(CANDIDATES))
        recovered_health = QuantTournamentBook(outbox_db).report(SYMBOL)[
            "horizons"
        ]["5m"]["health"]
        self.assertEqual(recovered_health["state"], "HEALTHY")
        self.assertEqual(recovered_health["pending_decision_outbox"], 0)


if __name__ == "__main__":
    unittest.main()
