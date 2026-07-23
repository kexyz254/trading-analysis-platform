import hashlib
import json
import math
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Iterator

from webapp.quant_tournament import (
    CANDIDATES,
    MIN_ELIGIBLE_TRADES,
    QuantStrategyTournament,
    QuantTournamentBook,
)
from webapp.market_state import ClosedCandle


DEPTH = {"bids": [["99.99", "90"]], "asks": [["100.01", "10"]]}
STRONG_TICKS = [
    {"price": 99.0 + index / 9.0, "trade_ms": 1_000 + index}
    for index in range(10)
]


@contextmanager
def sqlite_connection(path: str) -> Iterator[sqlite3.Connection]:
    """Commit or roll back, then release the file handle on every platform."""
    conn = sqlite3.connect(path)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


class QuantTournamentTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self._tmp.name) / "analytics.db")
        with sqlite_connection(self.db_path) as conn:
            conn.execute("CREATE TABLE price_calls (id INTEGER PRIMARY KEY)")
            conn.execute("INSERT INTO price_calls(id) VALUES (1)")
        self.tournament = QuantStrategyTournament(
            self.db_path,
            fee_rate=Decimal("0.001"),
            slippage_rate=Decimal("0.0002"),
            clock_ms=lambda: 2_000_000,
        )

    def tearDown(self):
        self._tmp.cleanup()

    def rows(self, query, params=()):
        with sqlite_connection(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(row) for row in conn.execute(query, params)]

    def record_cycle(self):
        return self.tournament.record_cycle_decisions(
            price_call_id=1,
            symbol="BTC/USDT",
            horizon="5m",
            cycle_start_ms=300_000,
            due_at_ms=600_000,
            decision_at_ms=300_010,
            entry_price_text="100.00000000",
            orderbook_depth=DEPTH,
            recent_ticks=STRONG_TICKS,
            base_probability_up=0.80,
        )

    def warm_model_history(self, count=120):
        interval = 300_000
        cycle_start = count * interval
        candles = []
        for index in range(count):
            base = 100.0 + 0.025 * index + 0.8 * math.sin(index / 3.0)
            close = base + 0.35 * math.sin(index * 1.7)
            candles.append(
                ClosedCandle(
                    symbol="BTC/USDT",
                    horizon="5m",
                    open_time_ms=index * interval,
                    close_time_ms=(index + 1) * interval - 1,
                    open_text=str(base),
                    high_text=str(max(base, close) + 0.2),
                    low_text=str(min(base, close) - 0.2),
                    close_text=str(close),
                    volume_text="10",
                )
            )
        inserted = self.tournament.market_state.ingest_closed_candles(
            candles,
            observed_at_ms=cycle_start,
            source="TEST_WARMUP",
            evidence_role="WARMUP",
        )
        self.assertEqual(inserted, count)
        return cycle_start, candles[-1].close_text

    def test_cycle_records_every_real_candidate_once_and_is_idempotent(self):
        self.assertEqual(self.record_cycle(), len(CANDIDATES))
        self.assertEqual(self.record_cycle(), 0)
        decisions = self.rows("SELECT * FROM quant_candidate_decisions ORDER BY candidate_name")
        self.assertEqual(len(decisions), len(CANDIDATES))
        self.assertEqual({row["action"] for row in decisions} - {"UP", "DOWN", "NO_TRADE"}, set())
        self.assertTrue(all(json.loads(row["feature_snapshot"])["current_price"] == 100.0 for row in decisions))

    def test_rolling_ml_has_stable_identity_and_safe_learning_telemetry(self):
        cycle_start, entry = self.warm_model_history()
        inserted = self.tournament.record_cycle_decisions(
            price_call_id=1,
            symbol="BTC/USDT",
            horizon="5m",
            cycle_start_ms=cycle_start,
            due_at_ms=cycle_start + 300_000,
            decision_at_ms=cycle_start + 10,
            entry_price_text=entry,
            orderbook_depth=DEPTH,
            recent_ticks=STRONG_TICKS,
            base_probability_up=0.55,
        )
        self.assertEqual(inserted, len(CANDIDATES))
        row = self.rows(
            "SELECT * FROM quant_candidate_decisions WHERE candidate_name = 'ml_ensemble'"
        )[0]
        self.assertEqual(row["model_training_samples"], 99)
        self.assertEqual(len(row["model_fingerprint"]), 64)
        self.assertEqual(row["model_trained_at_ms"], cycle_start + 10)
        self.assertLess(row["model_training_start_ms"], row["model_training_end_ms"])
        self.assertGreater(row["model_label_up_rate"], 0.0)
        self.assertLess(row["model_label_up_rate"], 1.0)

        learning = QuantTournamentBook(self.db_path).report("BTC/USDT")[
            "learning"
        ]["tournament_ml"]["horizons"]["5m"]
        self.assertEqual(learning["state"], "READY")
        self.assertEqual(learning["model_fingerprint"], row["model_fingerprint"])
        self.assertEqual(learning["feature_count"], 5)
        self.assertEqual(learning["settled_feedback_count"], 0)
        serialized = json.dumps(learning)
        self.assertNotIn("weights", serialized)
        self.assertNotIn("feature_snapshot", serialized)

        self.tournament.settle_cycle(
            symbol="BTC/USDT",
            horizon="5m",
            cycle_start_ms=cycle_start,
            due_at_ms=cycle_start + 300_000,
            open_price_text=entry,
            high_price_text=str(float(entry) + 1),
            low_price_text=str(float(entry) - 1),
            close_price_text=str(float(entry) + 0.25),
            settled_at_ms=cycle_start + 300_020,
            settlement_method="STREAM_KLINE",
        )
        settled_learning = QuantTournamentBook(self.db_path).report("BTC/USDT")[
            "learning"
        ]["tournament_ml"]["horizons"]["5m"]
        self.assertEqual(settled_learning["settled_feedback_count"], 1)
        self.assertEqual(
            settled_learning["last_feedback_at_ms"], cycle_start + 300_020
        )

    def test_rolling_ml_fingerprint_is_reproducible_and_content_addressed(self):
        closes = [100 + 0.03 * i + math.sin(i / 3) for i in range(120)]
        close_times = [(i + 1) * 300_000 - 1 for i in range(120)]
        first = self.tournament._rolling_logistic_probability(
            closes, closes[-1], close_times
        )
        second = self.tournament._rolling_logistic_probability(
            closes, closes[-1], close_times
        )
        changed = list(closes)
        changed[50] += 1.25
        third = self.tournament._rolling_logistic_probability(
            changed, changed[-1], close_times
        )
        self.assertEqual(first[1], 99)
        self.assertEqual(first[3], second[3])
        self.assertNotEqual(first[3], third[3])

    def test_neutral_trained_ml_is_ready_but_low_edge(self):
        cycle_start, entry = self.warm_model_history()
        assessment = self.tournament.market_state.assess_cycle(
            symbol="BTC/USDT",
            horizon="5m",
            cycle_start_ms=cycle_start,
            decision_at_ms=cycle_start + 10,
        )
        features = self.tournament._feature_snapshot(
            Decimal(entry), DEPTH, STRONG_TICKS, assessment
        )
        neutral = replace(features, ml_probability_up=0.5, trend_bps=0.0)
        candidate = next(item for item in CANDIDATES if item.name == "ml_ensemble")
        decision = self.tournament._candidate_decision(
            candidate, neutral, 0.5, 24.0
        )
        self.assertEqual(decision.action, "NO_TRADE")
        self.assertEqual(decision.reason_code, "MODEL_READY_LOW_EDGE")

    def test_post_decision_stream_extrema_and_costs_are_persisted(self):
        self.record_cycle()
        self.tournament.observe_trade("BTC/USDT", "100.80000000", 302_000)
        self.tournament.observe_trade("BTC/USDT", "99.70000000", 303_000)
        inserted = self.tournament.settle_cycle(
            symbol="BTC/USDT",
            horizon="5m",
            cycle_start_ms=300_000,
            due_at_ms=600_000,
            open_price_text="99.90000000",
            high_price_text="101.50000000",
            low_price_text="98.50000000",
            close_price_text="100.50000000",
            settled_at_ms=600_020,
            settlement_method="STREAM_KLINE",
        )
        self.assertEqual(inserted, len(CANDIDATES))
        outcomes = self.rows(
            """
            SELECT d.action, o.* FROM quant_candidate_outcomes o
            JOIN quant_candidate_decisions d ON d.id = o.decision_id
            ORDER BY d.candidate_name
            """
        )
        self.assertTrue(all(row["excursion_source"] == "STREAM_AGG_TRADE_AFTER_DECISION" for row in outcomes))
        traded = [row for row in outcomes if row["eligible_trade"]]
        no_trade = [row for row in outcomes if not row["eligible_trade"]]
        self.assertTrue(traded)
        self.assertTrue(no_trade)
        for row in traded:
            expected_mfe, expected_mae = (
                (80.0, 30.0) if row["action"] == "UP" else (30.0, 80.0)
            )
            self.assertAlmostEqual(row["mfe_bps"], expected_mfe, places=6)
            self.assertAlmostEqual(row["mae_bps"], expected_mae, places=6)
            self.assertGreater(row["cost_bps"], 23.9)
        self.assertTrue(all(row["net_pnl_text"] == "0" for row in no_trade))
        self.assertEqual(len(self.rows("SELECT * FROM quant_candidate_equity")), len(CANDIDATES))

    def test_small_sample_cannot_create_a_champion(self):
        self.record_cycle()
        self.tournament.settle_cycle(
            symbol="BTC/USDT",
            horizon="5m",
            cycle_start_ms=300_000,
            due_at_ms=600_000,
            open_price_text="100", high_price_text="101", low_price_text="99",
            close_price_text="100.5", settled_at_ms=600_020,
            settlement_method="STREAM_KLINE",
        )
        report = self.tournament.evaluate("BTC/USDT", "5m")
        self.assertEqual(report["regimes"]["unclassified"]["qualified_count"], 0)
        dashboard = QuantTournamentBook(self.db_path).report("BTC/USDT")
        self.assertIsNone(dashboard["horizons"]["5m"]["champion"])

    def test_dashboard_reports_complete_roster_integrity_and_lifecycle(self):
        self.record_cycle()
        self.tournament.settle_cycle(
            symbol="BTC/USDT",
            horizon="5m",
            cycle_start_ms=300_000,
            due_at_ms=600_000,
            open_price_text="100",
            high_price_text="101",
            low_price_text="99",
            close_price_text="100.5",
            settled_at_ms=600_020,
            settlement_method="STREAM_KLINE",
        )

        dashboard = QuantTournamentBook(self.db_path).report("BTC/USDT")
        horizon = dashboard["horizons"]["5m"]
        health = horizon["health"]
        self.assertEqual(health["state"], "HEALTHY")
        self.assertEqual(health["active_candidates"], len(CANDIDATES))
        self.assertEqual(health["recorded_cycles"], 1)
        self.assertEqual(health["settled_cycles"], 1)
        self.assertEqual(health["finalized_cycles"], 1)
        self.assertEqual(health["active_cycles"], 0)
        self.assertEqual(health["expected_decision_records"], len(CANDIDATES))
        self.assertEqual(health["actual_decision_records"], len(CANDIDATES))
        self.assertEqual(health["missing_decision_records"], 0)
        self.assertEqual(health["duplicate_decision_groups"], 0)
        self.assertEqual(health["settled_candidate_decisions"], len(CANDIDATES))
        self.assertEqual(
            health["expected_finalized_decision_records"], len(CANDIDATES)
        )
        self.assertEqual(
            health["actual_finalized_decision_records"], len(CANDIDATES)
        )
        self.assertEqual(health["missing_outcome_records"], 0)
        self.assertEqual(len(horizon["candidates"]), len(CANDIDATES))
        self.assertEqual(
            {candidate["lifecycle"] for candidate in horizon["candidates"]},
            {"COLLECTING"},
        )
        self.assertEqual(
            sum(health["lifecycle_counts"].values()), len(CANDIDATES)
        )

    def test_dashboard_separates_active_cycles_from_settled_evidence(self):
        self.record_cycle()

        dashboard = QuantTournamentBook(self.db_path).report("BTC/USDT")
        horizon = dashboard["horizons"]["5m"]
        health = horizon["health"]

        self.assertEqual(health["state"], "HEALTHY")
        self.assertEqual(health["recorded_cycles"], 1)
        self.assertEqual(health["settled_cycles"], 0)
        self.assertEqual(health["finalized_cycles"], 0)
        self.assertEqual(health["active_cycles"], 1)
        self.assertEqual(health["actual_decision_records"], len(CANDIDATES))
        self.assertEqual(health["pending_candidate_decisions"], len(CANDIDATES))
        self.assertEqual(health["expected_finalized_decision_records"], 0)
        self.assertEqual(health["actual_finalized_decision_records"], 0)
        self.assertEqual(health["missing_outcome_records"], 0)

    def test_dashboard_degrades_when_a_finalized_cycle_has_missing_outcomes(self):
        self.record_cycle()
        decision_id = self.rows(
            "SELECT id FROM quant_candidate_decisions ORDER BY id LIMIT 1"
        )[0]["id"]
        with sqlite_connection(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO quant_candidate_outcomes (
                    decision_id, status, settled_at_ms, eligible_trade,
                    settlement_method, void_reason
                ) VALUES (?, 'VOID', ?, 0, 'INTEGRITY_TEST', 'partial cycle')
                """,
                (decision_id, 600_020),
            )

        health = QuantTournamentBook(self.db_path).report("BTC/USDT")[
            "horizons"
        ]["5m"]["health"]
        self.assertEqual(health["state"], "DEGRADED")
        self.assertEqual(health["finalized_cycles"], 1)
        self.assertEqual(health["expected_finalized_decision_records"], len(CANDIDATES))
        self.assertEqual(health["actual_finalized_decision_records"], 1)
        self.assertEqual(health["missing_outcome_records"], len(CANDIDATES) - 1)

    def test_dashboard_contract_does_not_expose_proprietary_decision_inputs(self):
        self.record_cycle()
        dashboard = QuantTournamentBook(self.db_path).report("BTC/USDT")
        for horizon in dashboard["horizons"].values():
            for candidate in horizon["candidates"]:
                self.assertNotIn("feature_snapshot", candidate)
                self.assertNotIn("decision_reason", candidate)
        self.assertEqual(
            dashboard["confidentiality"]["exposure"],
            "AGGREGATED_METRICS_ONLY",
        )
        serialized = json.dumps(dashboard)
        self.assertNotIn("orderbook_json", serialized)
        self.assertNotIn("recent_ticks_json", serialized)
        self.assertNotIn("composite_weights", serialized)

    def test_confidential_dashboard_assets_are_local_and_pinned(self):
        root = Path(__file__).resolve().parents[1]
        index = (root / "webapp" / "pro_static" / "index.html").read_text(
            encoding="utf-8"
        )
        self.assertNotIn('src="http://', index)
        self.assertNotIn('src="https://', index)
        self.assertIn(
            '/pro/static/vendor/lightweight-charts-4.2.0.min.js', index
        )

        chart_asset = root / "webapp" / "pro_static" / "vendor" / (
            "lightweight-charts-4.2.0.min.js"
        )
        self.assertEqual(
            hashlib.sha256(chart_asset.read_bytes()).hexdigest(),
            "46fc69534ec098f095bbcd1d9a26d693d39a8b9eeff7343536765b3dd28c2bdf",
        )
        server = (root / "webapp" / "server.py").read_text(encoding="utf-8")
        self.assertIn('response.headers["Content-Security-Policy"]', server)
        self.assertIn("frame-ancestors 'none'", server)
        pro_api = server[server.index('@app.get("/api/pro/ohlcv/') :]
        self.assertNotIn("detail=str(exc)", pro_api)
        self.assertNotIn("detail=f\"Could", pro_api)
        self.assertIn(
            "Confidential research service is temporarily unavailable.",
            server,
        )

    def test_database_contexts_explicitly_close_connections(self):
        with self.tournament._connect() as tournament_conn:
            self.assertEqual(tournament_conn.execute("SELECT 1").fetchone()[0], 1)
        with self.assertRaises(sqlite3.ProgrammingError):
            tournament_conn.execute("SELECT 1")

        book = QuantTournamentBook(self.db_path)
        with book._connect() as report_conn:
            self.assertEqual(report_conn.execute("SELECT 1").fetchone()[0], 1)
        with self.assertRaises(sqlite3.ProgrammingError):
            report_conn.execute("SELECT 1")

    def test_append_only_evidence_rejects_update_and_delete(self):
        self.record_cycle()
        with self.assertRaises(sqlite3.IntegrityError):
            with sqlite_connection(self.db_path) as conn:
                conn.execute("UPDATE quant_candidate_decisions SET action='DOWN' WHERE id=1")
        with self.assertRaises(sqlite3.IntegrityError):
            with sqlite_connection(self.db_path) as conn:
                conn.execute("DELETE FROM quant_candidate_decisions WHERE id=1")

    def test_candidate_is_promoted_only_after_every_gate_passes(self):
        candidate = CANDIDATES[0]
        with sqlite_connection(self.db_path) as conn:
            for index in range(MIN_ELIGIBLE_TRADES):
                due = 1_000_000 + index * 300_000
                decision = conn.execute(
                    """
                    INSERT INTO quant_candidate_decisions (
                        price_call_id, symbol, horizon_label, cycle_start_ms,
                        due_at_ms, decision_at_ms, candidate_name,
                        candidate_version, tournament_version, regime, action,
                        entry_price_text, probability_up, confidence,
                        expected_move_bps, decision_reason, feature_snapshot,
                        fee_rate_text, slippage_rate_text,
                        evidence_policy_version, timing_class,
                        issue_delay_ms, forecast_lead_ms,
                        forecast_lead_ratio, primary_benchmark_eligible
                    ) VALUES (1, 'BTC/USDT', '5m', ?, ?, ?, ?, ?, 'test',
                              'trending_up', 'UP', '100', 0.70, 0.40, 50,
                              'synthetic deterministic test evidence', '{}',
                              '0.001', '0.0002',
                              'economic-evidence-v1.3.0', 'BOUNDARY_ISSUED',
                              10, 299990, 0.9999666667, 1)
                    """,
                    (due - 300_000, due, due - 299_990, candidate.name, candidate.version),
                ).lastrowid
                conn.execute(
                    """
                    INSERT INTO quant_candidate_outcomes (
                        decision_id, status, settled_at_ms, actual_direction,
                        open_text, high_text, low_text, close_text, mfe_bps,
                        mae_bps, gross_return_bps, cost_bps, net_return_bps,
                        gross_pnl_text, fee_cost_text, slippage_cost_text,
                        net_pnl_text, correct, eligible_trade, excursion_source,
                        settlement_method
                    ) VALUES (?, 'SETTLED', ?, 'UP', '100', '101', '99.9',
                              '100.5', 100, 10, 50, 24, 26, '0.005',
                              '0.002', '0.0004', '0.0026', 1, 1,
                              'TEST_FIXTURE', 'TEST')
                    """,
                    (decision, due + 20),
                )
            conn.commit()

        report = self.tournament.evaluate("BTC/USDT", "5m")
        regime = report["regimes"]["trending_up"]
        self.assertEqual(regime["best_candidate"], candidate.name)
        metrics = regime["candidates"][0]
        self.assertTrue(metrics["qualified"])
        self.assertTrue(all(metrics["gates"].values()))
        champion = QuantTournamentBook(self.db_path).report("BTC/USDT")["horizons"]["5m"]["champion"]
        self.assertEqual(champion["name"], candidate.name)
        self.assertGreater(champion["confidence"], 0)


if __name__ == "__main__":
    unittest.main()
