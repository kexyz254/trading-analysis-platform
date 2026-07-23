from __future__ import annotations

import unittest
from unittest.mock import patch

from webapp import learning_observability as observability


TOURNAMENT_REPORT = {
    "learning": {
        "tournament_ml": {
            "source": "ROLLING_FORWARD_ONLY_LOGISTIC_ENSEMBLE",
            "horizons": {"5m": {"state": "READY"}},
        }
    }
}


class LearningObservabilityTest(unittest.TestCase):
    def test_three_paths_remain_explicit_and_interpreter_has_no_authority(self):
        with (
            patch.object(
                observability,
                "_legacy_health",
                return_value={"state": "ACTIVE", "execution_authority": True},
            ),
            patch.object(
                observability,
                "_forecast_health",
                return_value={"state": "READY"},
            ),
            patch.object(
                observability,
                "_tournament_report",
                return_value=TOURNAMENT_REPORT,
            ) as tournament_report,
        ):
            report = observability.build_learning_health(
                "legacy.db",
                "btc/usdt",
                tournament_db_path="analytics.db",
                forecast_timeframe="5m",
                legacy_timeframe="1h",
                retrain_interval_hours=24,
            )

        self.assertEqual(report["state"], "ACTIVE")
        self.assertEqual(
            set(report["paths"]),
            {"legacy_learning", "forecast_ml", "tournament_ml"},
        )
        contract = report["separation_contract"]
        self.assertTrue(contract["paths_are_independent"])
        self.assertTrue(contract["health_is_not_inferred_from_process_error_rate"])
        self.assertFalse(contract["interpreter_execution_authority"])
        self.assertFalse(contract["interpreter_promotion_authority"])
        self.assertFalse(report["paths"]["forecast_ml"]["execution_authority"])
        self.assertFalse(report["paths"]["tournament_ml"]["execution_authority"])
        tournament_report.assert_called_once_with(
            "analytics.db",
            "BTC/USDT",
        )

    def test_colocated_schema_fallback_remains_supported(self):
        with (
            patch.object(
                observability,
                "_legacy_health",
                return_value={"state": "NO_EVIDENCE"},
            ),
            patch.object(
                observability,
                "_forecast_health",
                return_value={"state": "WARMING"},
            ),
            patch.object(
                observability,
                "_tournament_report",
                return_value=TOURNAMENT_REPORT,
            ) as tournament_report,
        ):
            observability.build_learning_health(
                "combined.db",
                "BTC/USDT",
                forecast_timeframe="5m",
                legacy_timeframe="1h",
                retrain_interval_hours=24,
            )

        tournament_report.assert_called_once_with("combined.db", "BTC/USDT")


if __name__ == "__main__":
    unittest.main()
