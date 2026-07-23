from __future__ import annotations

import unittest

from webapp.market_interpreter import build_market_interpretation


NOW = 1_800_000_000_000


def tournament(regime="TRENDING_DOWN", confidence=0.8, ml_state="READY"):
    return {
        "market_state": {
            "horizons": {
                "5m": {
                    "data_state": "READY",
                    "regime": regime,
                    "regime_confidence": confidence,
                    "latest_close_time_ms": NOW - 1,
                }
            }
        },
        "learning": {
            "tournament_ml": {
                "horizons": {
                    "5m": {
                        "state": ml_state,
                        "decision_state": "MODEL_READY_LOW_EDGE",
                        "training_samples": 99,
                        "model_fingerprint": "a" * 64,
                        "last_inference_at_ms": NOW - 10,
                    }
                }
            }
        },
        "horizons": {"5m": {"health": {"state": "HEALTHY"}}},
    }


def forecast(direction="DOWN", probability_up=0.30, hit_rate=0.65):
    return {
        "as_of": NOW - 1,
        "model_fingerprint": "b" * 64,
        "horizons": [
            {
                "step": step,
                "direction": direction,
                "probability_up": probability_up,
                "hit_rate": hit_rate,
                "up_share_holdout": 0.50,
            }
            for step in (1, 2, 3)
        ],
    }


class MarketInterpreterTest(unittest.TestCase):
    def test_aligned_bearish_sources_do_not_override_no_trade(self):
        report = build_market_interpretation(
            symbol="btc/usdt",
            timeframe="5m",
            engine_timeframe="5m",
            legacy_prediction={
                "signal": "SELL", "confidence": 0.8, "timestamp": NOW - 1
            },
            forecast=forecast(),
            patterns=[
                {"name": "double_top", "bias": "bearish", "confidence": 0.9}
            ],
            tournament=tournament(),
            open_calls=[
                {
                    "horizon_label": "5m",
                    "direction": "DOWN",
                    "economic_action": "NO_TRADE",
                    "economic_decision_code": "COST_HURDLE",
                    "primary_benchmark_eligible": 1,
                    "timing_class": "BOUNDARY_ISSUED",
                }
            ],
            now_ms=NOW,
        )
        self.assertEqual(report["state"], "READY")
        self.assertEqual(report["outlook"], "BEARISH")
        self.assertIn(report["conviction"], {"MODERATE", "HIGH"})
        self.assertEqual(report["economics"]["state"], "ABSTAIN")
        self.assertIn("NO TRADE", report["summary"])
        self.assertFalse(report["alignment_score_is_probability"])
        self.assertTrue(report["authority"]["read_only"])
        self.assertFalse(report["authority"]["can_open_exposure"])

    def test_directional_disagreement_is_explicitly_mixed(self):
        report = build_market_interpretation(
            symbol="BTC/USDT",
            timeframe="5m",
            engine_timeframe="1h",
            legacy_prediction={"signal": "BUY", "confidence": 0.9},
            forecast=forecast(direction="UP", probability_up=0.75),
            patterns=[],
            tournament=tournament(regime="TRENDING_DOWN", confidence=0.9),
            now_ms=NOW,
        )
        self.assertEqual(report["outlook"], "MIXED")
        self.assertIn("DIRECTIONAL_SOURCES_DISAGREE", report["conflicts"])
        self.assertIn("REGIME_FORECAST_DIVERGENCE", report["conflicts"])
        self.assertIn("LEGACY_SIGNAL_TIMEFRAME_MISMATCH", report["warnings"])
        self.assertEqual(
            report["ai_paths"]["legacy_strategy"]["state"], "CONTEXT_ONLY"
        )

    def test_forecast_without_holdout_edge_cannot_drive_outlook(self):
        report = build_market_interpretation(
            symbol="BTC/USDT",
            timeframe="5m",
            engine_timeframe="1h",
            forecast=forecast(hit_rate=0.49),
            tournament={},
            now_ms=NOW,
        )
        self.assertEqual(
            report["ai_paths"]["forecast_ml"]["state"], "READY_LOW_EDGE"
        )
        self.assertIn("FORECAST_ML_NO_VALIDATED_EDGE", report["warnings"])
        self.assertEqual(report["outlook"], "INSUFFICIENT_EVIDENCE")

    def test_non_tournament_timeframe_remains_useful_but_partial(self):
        report = build_market_interpretation(
            symbol="BTC/USDT",
            timeframe="1h",
            engine_timeframe="1h",
            forecast=forecast(direction="UP", probability_up=0.8),
            tournament=tournament(regime="TRENDING_UP"),
            now_ms=NOW,
        )
        self.assertEqual(report["state"], "PARTIAL")
        self.assertEqual(
            report["economics"]["state"], "NO_MATCHING_ECONOMIC_HORIZON"
        )
        self.assertIn(
            "MARKET_STATE_NOT_AVAILABLE_FOR_SELECTED_TIMEFRAME",
            report["warnings"],
        )

    def test_source_failures_and_private_inputs_fail_closed(self):
        report = build_market_interpretation(
            symbol="BTC/USDT",
            timeframe="15m",
            engine_timeframe="1h",
            source_errors=["PATTERN_SOURCE_UNAVAILABLE"],
            now_ms=NOW,
        )
        self.assertEqual(report["state"], "NO_EVIDENCE")
        self.assertEqual(report["outlook"], "INSUFFICIENT_EVIDENCE")
        self.assertIn("PATTERN_SOURCE_UNAVAILABLE", report["warnings"])
        self.assertFalse(report["confidentiality"]["raw_feature_vectors_exposed"])
        self.assertFalse(report["confidentiality"]["private_thresholds_exposed"])


if __name__ == "__main__":
    unittest.main()
