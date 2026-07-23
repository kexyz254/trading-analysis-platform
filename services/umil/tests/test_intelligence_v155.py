from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from services.umil.app.commentary import (
    Commentary,
    EvidenceClaim,
    deterministic_commentary,
    validate_commentary,
)
from services.umil.app.derivatives import calculate_derivatives_metrics
from services.umil.app.dna import build_market_dna
from services.umil.app.evidence import EvidenceLedger
from services.umil.app.features import calculate_timeframe_features
from services.umil.app.intelligence import IntelligenceEngine
from services.umil.app.lightweight import calculate_lightweight_states
from services.umil.app.scale import normalize_market_scale
from services.umil.app.tiering import assign_tiers
from services.umil.app.microstructure import (
    assess_pressure,
    calculate_book_metrics,
    calculate_trade_flow,
)
from services.umil.app.normalization import blended_percentile, robust_zscore
from services.umil.app.observations import (
    AggTrade,
    BookLevel,
    Candle,
    DerivativesObservation,
    OrderBookSnapshot,
)
from services.umil.app.watch_conditions import (
    WatchState,
    create_structure_watches,
    evaluate_condition,
)
from services.umil.tests.fixtures import ticker
from services.umil_client.validator import MarketIntelligenceValidator


NOW = datetime(2026, 7, 22, 16, 0, tzinfo=timezone.utc)
INSTRUMENT = "binance:spot:BTCUSDT"


def candles(interval: str = "5m", count: int = 80) -> tuple[Candle, ...]:
    rows = []
    price = 100.0
    for index in range(count):
        start = NOW - timedelta(minutes=5 * (count - index))
        close = price * (1.002 if index % 5 else 0.999)
        volume = 100_000.0 + index * 1000.0
        rows.append(Candle(
            instrument_id=INSTRUMENT,
            interval=interval,
            open_time=start,
            close_time=start + timedelta(minutes=5),
            open=price,
            high=max(price, close) * 1.002,
            low=min(price, close) * 0.998,
            close=close,
            base_volume=volume / price,
            quote_volume=volume,
            trade_count=100 + index,
            taker_buy_quote_volume=volume * 0.58,
            closed=True,
        ))
        price = close
    return tuple(rows)


class FeatureAndDNATest(unittest.TestCase):
    def test_closed_candle_features_are_complete_and_finite(self):
        result = calculate_timeframe_features(candles())
        self.assertEqual(result.sample_count, 80)
        self.assertIsNotNone(result.rsi_14)
        self.assertIsNotNone(result.atr_pct)
        self.assertIsNotNone(result.realized_volatility_pct)
        self.assertGreater(result.completeness, 0.99)

    def test_open_candle_is_excluded(self):
        rows = list(candles())
        row = rows[-1]
        rows[-1] = Candle(
            instrument_id=row.instrument_id, interval=row.interval,
            open_time=row.open_time, close_time=row.close_time,
            open=row.open, high=row.high, low=row.low, close=row.close,
            base_volume=row.base_volume, quote_volume=row.quote_volume,
            trade_count=row.trade_count,
            taker_buy_quote_volume=row.taker_buy_quote_volume, closed=False,
        )
        result = calculate_timeframe_features(rows)
        self.assertEqual(result.sample_count, 79)

    def test_robust_normalization_resists_extreme_tail(self):
        history = list(range(100)) + [1_000_000]
        score = robust_zscore(50, history)
        self.assertLess(abs(score), 1.0)
        blended = blended_percentile(90, history=range(100), peers=range(100))
        self.assertGreater(blended.score, 85)

    def test_market_dna_has_eight_independent_dimensions(self):
        feature = calculate_timeframe_features(candles())
        dna = build_market_dna(
            snapshot_id="snapshot-1", instrument_id=INSTRUMENT,
            as_of=NOW, timeframes={"5m": feature}, evidence_ids=("e1",),
        )
        payload = dna.as_dict()
        self.assertEqual(set(payload["dna"]), {"TRN", "MOM", "PRS", "STR", "VOL", "LIQ", "ACT", "RSK"})
        self.assertIsNone(payload["directional_probability"])
        self.assertLess(dna.confidence, 75)

    def test_all_symbol_state_exposes_missing_dimensions_without_neutral_dna(self):
        states = calculate_lightweight_states(
            (ticker("BTCUSDT", 105, 100, 1_000_000, event_time=NOW),),
            as_of=NOW, stale_after_seconds=15,
        )
        state = states[INSTRUMENT]
        self.assertEqual(state["trend"], "ADVANCING")
        self.assertIn("pressure", state["missing_dimensions"])
        self.assertIsNone(state["directional_probability"])

    def test_market_cap_symbol_ambiguity_is_never_hidden(self):
        rows = [
            {"id": "alpha", "symbol": "abc", "market_cap": 100, "market_cap_rank": 10},
            {"id": "another-abc", "symbol": "abc", "market_cap": 50, "market_cap_rank": 20},
        ]
        normalized, ambiguous = normalize_market_scale(rows, observed_at=NOW)
        self.assertEqual(normalized["ABC"].asset_id, "alpha")
        self.assertIn("ABC", ambiguous)

    def test_one_thousand_asset_universe_is_complete_and_bounded(self):
        universe = tuple(
            ticker(
                f"A{index:04d}USDT", 10 + index / 1000, 10,
                1_000_000 + index, event_time=NOW,
            )
            for index in range(1000)
        )
        states = calculate_lightweight_states(
            universe, as_of=NOW, stale_after_seconds=15,
        )
        tiers = assign_tiers(universe, active_limit=100, focus_limit=20)
        self.assertEqual(len(states), 1000)
        self.assertEqual(sum(row.tier == "FOCUS" for row in tiers), 20)
        self.assertEqual(sum(row.tier == "ACTIVE" for row in tiers), 80)
        self.assertEqual(sum(row.tier == "UNIVERSE" for row in tiers), 900)


class MicrostructureAndDerivativesTest(unittest.TestCase):
    def test_pressure_uses_signed_trades_and_book_depth(self):
        trades = tuple(AggTrade(
            INSTRUMENT, index, NOW - timedelta(seconds=20 - index),
            100 + index * 0.01, 2.0, buyer_is_maker=index < 3,
        ) for index in range(20))
        flow = calculate_trade_flow(trades, as_of=NOW)
        book = OrderBookSnapshot(
            INSTRUMENT, NOW, 10,
            bids=(BookLevel(99.9, 200), BookLevel(99.8, 100)),
            asks=(BookLevel(100.1, 50), BookLevel(100.2, 50)),
        )
        book_metrics = calculate_book_metrics(book, target_quote_notional=1000)
        pressure = assess_pressure(flow, book_metrics)
        self.assertGreater(flow.buy_notional, flow.sell_notional)
        self.assertGreater(book_metrics.depth_imbalance, 0)
        self.assertEqual(pressure.label, "BUYER_DOMINANT")

    def test_derivatives_crowding_is_not_directional_probability(self):
        metrics = calculate_derivatives_metrics(DerivativesObservation(
            "binance:perpetual:BTCUSDT", NOW, 101, 100, 0.0015, 1200, 1000,
        ))
        self.assertGreater(metrics.crowding_score, 40)
        self.assertIn("EXTREME_FUNDING", metrics.risk_flags)


class GovernanceReplayAndWatchTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.ledger = EvidenceLedger(Path(self.temp.name) / "umil.db")

    def tearDown(self):
        self.temp.cleanup()

    def test_intelligence_is_append_only_and_replayable_at_received_time(self):
        engine = IntelligenceEngine(self.ledger)
        first = engine.analyze(
            instrument_id=INSTRUMENT, candles_by_interval={"5m": candles()}, as_of=NOW,
        )
        first_record = self.ledger.get(first["evidence_record_id"])
        self.assertIsNotNone(first_record)
        cutoff = datetime.fromisoformat(first_record.received_at.replace("Z", "+00:00"))
        known = self.ledger.latest_as_of(
            event_type="MARKET_INTELLIGENCE_SNAPSHOT",
            instrument_id=INSTRUMENT, as_of=cutoff,
        )
        self.assertEqual(known.evidence_id, first["evidence_record_id"])
        history = self.ledger.history(
            event_type="MARKET_INTELLIGENCE_SNAPSHOT", instrument_id=INSTRUMENT,
        )
        self.assertEqual(len(history), 1)
        validator = MarketIntelligenceValidator(max_age_seconds=3600)
        accepted = validator.validate(first, received_at=NOW)
        self.assertTrue(accepted.accepted, accepted.reason)

        escalated = dict(first)
        escalated["authority"] = dict(first["authority"])
        escalated["authority"]["can_influence_ats"] = True
        self.assertFalse(validator.validate(escalated, received_at=NOW).accepted)

    def test_commentary_rejects_unsupported_evidence_and_promises(self):
        commentary = Commentary(
            "This will profit. Buy now.",
            (EvidenceClaim("bad", "Unsupported", ("made_up",), ("unknown",)),),
        )
        valid, errors = validate_commentary(
            commentary, allowed_evidence_ids={"e1"}, allowed_evidence_keys={"rsi_14"},
        )
        self.assertFalse(valid)
        self.assertIn("PROHIBITED_CLAIM", errors)

    def test_watch_condition_triggers_and_preserves_source_threshold(self):
        feature = calculate_timeframe_features(candles())
        watch = create_structure_watches(
            INSTRUMENT, feature, current_price=100, range_high=105, range_low=95,
            created_at=NOW, evidence_ids=("e1",),
        )[0]
        resolved = evaluate_condition(watch, {"last_price": 106}, as_of=NOW + timedelta(minutes=5))
        self.assertEqual(resolved.state, WatchState.TRIGGERED)
        self.assertEqual(resolved.threshold, 105)


if __name__ == "__main__":
    unittest.main()
