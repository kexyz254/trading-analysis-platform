from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from decimal import Decimal
from pathlib import Path

from webapp.economic_evidence import (
    ECONOMIC_EVIDENCE_VERSION,
    assess_economic_action,
    assess_issue_timing,
    estimate_expected_move_bps,
)
from webapp.price_call import PriceCallBook, RealTimePriceCallEngine
from webapp.quant_tournament import CANDIDATES, QuantTournamentBook


SYMBOL = "BTC/USDT"
DEPTH = {"bids": [["100.00", "90"]], "asks": [["100.01", "10"]]}


def _ticks(start_ms: int, *, flat: bool = False) -> list[dict]:
    return [
        {
            "price": 100.0 if flat else 100.0 + index * 0.00001,
            "trade_ms": start_ms + index * 100,
            "event_ms": start_ms + index * 100,
        }
        for index in range(10)
    ]


def _rows(db_path: str, query: str, params=()) -> list[dict]:
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(query, params)]


class EconomicEvidenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp.name) / "analytics.db")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_boundary_and_late_issuance_are_deterministic(self):
        boundary = assess_issue_timing(
            horizon_ms=300_000,
            cycle_start_ms=1_000_000,
            due_at_ms=1_300_000,
            decision_at_ms=1_010_000,
        )
        late = assess_issue_timing(
            horizon_ms=300_000,
            cycle_start_ms=1_000_000,
            due_at_ms=1_300_000,
            decision_at_ms=1_010_001,
        )
        self.assertTrue(boundary.primary_eligible)
        self.assertEqual(boundary.timing_class, "BOUNDARY_ISSUED")
        self.assertEqual(boundary.forecast_lead_ms, 290_000)
        self.assertFalse(late.primary_eligible)
        self.assertEqual(late.exclusion_code, "LATE_ISSUANCE")

    def test_invalid_or_expired_timing_fails_closed(self):
        assessment = assess_issue_timing(
            horizon_ms=300_000,
            cycle_start_ms=1_000_000,
            due_at_ms=1_300_001,
            decision_at_ms=1_300_001,
        )
        self.assertFalse(assessment.primary_eligible)
        self.assertEqual(assessment.timing_class, "INVALID_TIMING")

    def test_cost_hurdle_abstains_without_changing_the_directional_call(self):
        assessment = assess_economic_action(
            direction="UP",
            confidence=0.90,
            ticks=_ticks(1_000_000, flat=True),
            forecast_lead_ms=290_000,
            decision_at_ms=1_001_000,
            fee_rate=Decimal("0.001"),
            slippage_rate=Decimal("0.0002"),
            timing_eligible=True,
        )
        self.assertEqual(assessment.action, "NO_TRADE")
        self.assertEqual(assessment.decision_code, "COST_HURDLE")
        self.assertEqual(assessment.round_trip_cost_bps, 24.0)

    def test_movement_estimate_excludes_ticks_after_the_decision(self):
        known = _ticks(1_000_000, flat=True)
        contaminated = known + [
            {"price": 200.0, "trade_ms": 1_100_000, "event_ms": 1_100_000}
        ]
        clean = estimate_expected_move_bps(known, 290_000, 1_001_000)
        filtered = estimate_expected_move_bps(
            contaminated, 290_000, 1_001_000
        )
        self.assertEqual(clean, filtered)

    def test_late_call_is_preserved_but_cannot_open_exposure(self):
        engine = RealTimePriceCallEngine(self.db_path)
        cycle_start = 1_800_000_000_000
        decision_at = cycle_start + 120_000
        call_id = engine.cast_aligned_calls(
            SYMBOL,
            "100.00000000",
            decision_at + 1,
            decision_at,
            DEPTH,
            _ticks(decision_at - 1_000),
            ("5m",),
        )[0]
        call = _rows(
            self.db_path, "SELECT * FROM price_calls WHERE id = ?", (call_id,)
        )[0]
        self.assertEqual(call["timing_class"], "LATE_ISSUED")
        self.assertEqual(call["primary_benchmark_eligible"], 0)
        self.assertEqual(call["economic_action"], "NO_TRADE")
        self.assertEqual(
            _rows(
                self.db_path,
                "SELECT COUNT(*) AS total FROM paper_bankroll_positions",
            )[0]["total"],
            0,
        )
        decisions = _rows(
            self.db_path,
            "SELECT action, primary_benchmark_eligible FROM quant_candidate_decisions WHERE price_call_id = ?",
            (call_id,),
        )
        self.assertEqual(len(decisions), len(CANDIDATES))
        self.assertTrue(all(row["action"] == "NO_TRADE" for row in decisions))
        self.assertTrue(
            all(row["primary_benchmark_eligible"] == 0 for row in decisions)
        )

    def test_rest_close_is_valid_provenance_for_a_boundary_decision(self):
        engine = RealTimePriceCallEngine(self.db_path)
        cycle_start = 1_800_000_000_000
        decision_at = cycle_start + 5_000
        call_id = engine.cast_aligned_calls(
            SYMBOL,
            "100.00000000",
            decision_at + 1,
            decision_at,
            DEPTH,
            _ticks(decision_at - 1_000),
            ("5m",),
        )[0]
        due_at = _rows(
            self.db_path, "SELECT due_at FROM price_calls WHERE id = ?", (call_id,)
        )[0]["due_at"]
        engine.settle_closed_candle(
            SYMBOL,
            "5m",
            due_at - 300_000,
            due_at - 1,
            "100.50000000",
            due_at + 60_000,
            settlement_method="REST_RECOVERY",
        )
        report = PriceCallBook(self.db_path).economic_evidence(SYMBOL)["horizons"][
            "5m"
        ]
        self.assertEqual(report["primary_eligible_cycles"], 1)
        self.assertEqual(report["primary_excluded_cycles"], 0)
        self.assertEqual(
            report["settlement_provenance"]["REST_RECOVERY"]["sample"], 1
        )

    def test_primary_lead_time_excludes_late_issued_calls(self):
        engine = RealTimePriceCallEngine(self.db_path)
        first_cycle = 1_800_000_000_000
        boundary_at = first_cycle + 5_000
        engine.cast_aligned_calls(
            SYMBOL, "100", boundary_at + 1, boundary_at, DEPTH,
            _ticks(first_cycle), ("5m",),
        )
        engine.settle_closed_candle(
            SYMBOL, "5m", first_cycle, first_cycle + 299_999,
            "101", first_cycle + 300_020,
            settlement_method="STREAM_KLINE",
        )

        second_cycle = first_cycle + 300_000
        late_at = second_cycle + 120_000
        engine.cast_aligned_calls(
            SYMBOL, "101", late_at + 1, late_at, DEPTH,
            _ticks(second_cycle), ("5m",),
        )
        engine.settle_closed_candle(
            SYMBOL, "5m", second_cycle, second_cycle + 299_999,
            "100", second_cycle + 300_020,
            settlement_method="STREAM_KLINE",
        )

        report = PriceCallBook(self.db_path).economic_evidence(SYMBOL)[
            "horizons"
        ]["5m"]
        self.assertEqual(report["classified_cycles"], 2)
        self.assertEqual(report["primary_eligible_cycles"], 1)
        self.assertEqual(report["lead_time_ms"]["sample"], 1)
        self.assertEqual(report["lead_time_ms"]["scope"], "PRIMARY_ONLY")
        self.assertEqual(report["lead_time_ms"]["median"], 295_000)

    def test_pre_policy_rows_are_not_rewritten_or_promoted(self):
        engine = RealTimePriceCallEngine(self.db_path)
        with engine._connect() as conn:
            conn.execute(
                """
                INSERT INTO price_calls (
                    symbol, horizon_label, direction, price_to_beat,
                    actual_price, correct, miss_pct, created_at, due_at,
                    settled_at, voided, prob_up, prob_down, confidence,
                    legacy, engine_version, market_source, timing_mode,
                    evidence_class, entry_price_text, actual_price_text,
                    cycle_start_ms, cycle_close_ms, actual_direction,
                    settlement_method
                ) VALUES (
                    'BTC/USDT', '5m', 'UP', 100, 101, 1, 1, ?, ?, ?, 0,
                    .60, .40, .20, 0, 'v1.2.1', 'BINANCE_SPOT_MAINNET',
                    'EXCHANGE_BOUNDARY_MS', 'VALIDATED_BINANCE_CYCLE',
                    '100', '101', ?, ?, 'UP', 'STREAM_KLINE'
                )
                """,
                (1_800_000_000_000, 1_800_000_300_000, 1_800_000_300_010,
                 1_800_000_000_000, 1_800_000_299_999),
            )
        report = PriceCallBook(self.db_path).economic_evidence(SYMBOL)["horizons"][
            "5m"
        ]
        self.assertEqual(report["pre_policy_preserved_cycles"], 1)
        self.assertEqual(report["primary_eligible_cycles"], 0)
        preserved = _rows(
            self.db_path,
            "SELECT engine_version, evidence_policy_version FROM price_calls WHERE engine_version = 'v1.2.1'",
        )[0]
        self.assertEqual(preserved["engine_version"], "v1.2.1")
        self.assertIsNone(preserved["evidence_policy_version"])

    def test_public_call_contract_excludes_private_economic_inputs(self):
        engine = RealTimePriceCallEngine(self.db_path)
        cycle_start = 1_800_000_000_000
        engine.cast_aligned_calls(
            SYMBOL,
            "100.00000000",
            cycle_start + 1,
            cycle_start,
            DEPTH,
            _ticks(cycle_start - 1_000),
            ("5m",),
        )
        payload = json.dumps(PriceCallBook(self.db_path).open_call(SYMBOL))
        self.assertNotIn("expected_move_bps", payload)
        self.assertNotIn("confidence_adjusted_move_bps", payload)
        self.assertNotIn("cost_hurdle_bps", payload)
        self.assertNotIn("movement_model_version", payload)
        self.assertIn("economic_action", payload)

    def test_policy_link_tampering_degrades_health(self):
        engine = RealTimePriceCallEngine(self.db_path)
        cycle_start = 1_800_000_000_000
        call_id = engine.cast_aligned_calls(
            SYMBOL,
            "100.00000000",
            cycle_start + 1,
            cycle_start,
            DEPTH,
            _ticks(cycle_start - 1_000),
            ("5m",),
        )[0]
        with engine._connect() as conn:
            conn.execute(
                "UPDATE price_calls SET timing_class = 'LATE_ISSUED' WHERE id = ?",
                (call_id,),
            )
        health = QuantTournamentBook(self.db_path).report(SYMBOL)["horizons"][
            "5m"
        ]["health"]
        self.assertEqual(health["state"], "DEGRADED")
        self.assertEqual(health["broken_economic_policy_links"], len(CANDIDATES))

    def test_all_database_contexts_release_the_file_immediately(self):
        engine = RealTimePriceCallEngine(self.db_path)
        book = PriceCallBook(self.db_path)
        with engine._connect() as conn:
            self.assertEqual(conn.execute("SELECT 1").fetchone()[0], 1)
        with self.assertRaises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")
        with book._connect() as read_conn:
            self.assertEqual(read_conn.execute("SELECT 1").fetchone()[0], 1)
        with self.assertRaises(sqlite3.ProgrammingError):
            read_conn.execute("SELECT 1")


if __name__ == "__main__":
    unittest.main()
