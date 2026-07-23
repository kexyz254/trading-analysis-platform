from __future__ import annotations

import unittest

from services.umil.app.anomaly_calibration import calibrate_anomaly


class AnomalyCalibrationTest(unittest.TestCase):
    def _state(self, **overrides):
        value = {
            "state": "READY",
            "anomaly_score": 100.0,
            "return_percentile": 99.5,
            "volume_percentile": 98.0,
            "intraday_range_pct": 30.0,
            "range_position": 0.90,
            "quality_flags": ["SPREAD_UNAVAILABLE"],
            "available_dimensions": ["trend", "structure", "volatility", "activity"],
            "missing_dimensions": ["momentum", "pressure", "derivatives"],
        }
        value.update(overrides)
        return value

    def _detail(self, **overrides):
        value = {
            "anomaly_score": 100.0,
            "dna": {
                "TRN": 94.0, "MOM": 86.0, "PRS": 96.0, "STR": 88.0,
                "VOL": 96.0, "LIQ": 96.0, "ACT": 74.0, "RSK": 43.0,
            },
            "evidence_record_id": "6340a38a-3164-4736-8831-b8bafa9ffd90",
        }
        value.update(overrides)
        return value

    def test_saturated_raw_score_is_preserved_but_calibrated_below_ceiling(self):
        result = calibrate_anomaly(self._state(), self._detail())
        self.assertEqual(result["raw_anomaly_score"], 100.0)
        self.assertGreater(result["anomaly_score"], 0.0)
        self.assertLess(result["anomaly_score"], 100.0)
        self.assertFalse(result["trade_eligible"])
        self.assertEqual(result["usage"], "RESEARCH_ALERT")

    def test_different_saturated_signals_receive_different_scores(self):
        strong = calibrate_anomaly(self._state(), self._detail())
        weaker = calibrate_anomaly(
            self._state(
                return_percentile=65.0,
                volume_percentile=55.0,
                intraday_range_pct=5.0,
                range_position=0.55,
            ),
            self._detail(dna={key: 55.0 for key in self._detail()["dna"]}),
        )
        self.assertGreater(strong["anomaly_score"], weaker["anomaly_score"])

    def test_stale_universe_only_row_is_penalized(self):
        evidence_backed = calibrate_anomaly(self._state(), self._detail())
        stale_universe = calibrate_anomaly(
            self._state(
                state="STALE",
                quality_flags=["STALE_TICKER", "SPREAD_UNAVAILABLE"],
            ),
            None,
        )
        self.assertEqual(stale_universe["evidence_tier"], "UNIVERSE_ONLY")
        self.assertEqual(stale_universe["confidence_tier"], "LOW")
        self.assertGreater(evidence_backed["anomaly_score"], stale_universe["anomaly_score"])

    def test_complete_spread_evidence_improves_confidence(self):
        unavailable = calibrate_anomaly(self._state(), self._detail())
        available = calibrate_anomaly(
            self._state(quality_flags=[]),
            self._detail(),
        )
        self.assertGreater(available["confidence_score"], unavailable["confidence_score"])

    def test_calibration_does_not_mutate_inputs(self):
        state = self._state()
        detail = self._detail()
        state_before = repr(state)
        detail_before = repr(detail)
        calibrate_anomaly(state, detail)
        self.assertEqual(repr(state), state_before)
        self.assertEqual(repr(detail), detail_before)


if __name__ == "__main__":
    unittest.main()
