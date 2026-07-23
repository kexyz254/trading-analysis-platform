from __future__ import annotations

import copy
import unittest
from datetime import datetime, timezone

from services.umil.app.authority import AUTHORITY
from services.umil_client.validator import SnapshotValidator


NOW = datetime(2026, 7, 22, 16, 0, tzinfo=timezone.utc)


def valid_snapshot():
    return {
        "api_schema_version": "umil-api-v1",
        "snapshot_schema_version": "umil-snapshot-v1",
        "feature_schema_version": "umil-features-v1",
        "quality_schema_version": "umil-quality-v1",
        "as_of": NOW.isoformat(),
        "state": "READY",
        "eligible_instruments": 100,
        "observed_instruments": 100,
        "advancers": 60,
        "decliners": 35,
        "unchanged": 5,
        "advance_ratio": 0.60,
        "volume_weighted_advance_ratio": 0.58,
        "volume_concentration_hhi": 0.10,
        "quality": {
            "completeness": 1.0,
            "synchronization_quality": 1.0,
            "freshness_percentile": 95.0,
            "provider_latency_ms": {"binance": 10},
            "missing_providers": [],
            "stale_dimensions": [],
            "quality_flags": [],
        },
        "evidence_ids": ["7ea9c390-2625-4e79-9c50-2ea882f54620"],
        "authority": AUTHORITY.as_dict(),
    }


class ClientValidatorTest(unittest.TestCase):
    def setUp(self):
        self.validator = SnapshotValidator(max_age_seconds=20)

    def test_accepts_compatible_fresh_fail_closed_snapshot(self):
        result = self.validator.validate(valid_snapshot(), received_at=NOW)
        self.assertTrue(result.accepted)

    def test_rejects_authority_escalation(self):
        snapshot = valid_snapshot()
        snapshot["authority"]["can_trade"] = True
        result = self.validator.validate(snapshot, received_at=NOW)
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "AUTHORITY_ESCALATION")

    def test_rejects_malformed_count_reconciliation(self):
        snapshot = valid_snapshot()
        snapshot["advancers"] = 99
        result = self.validator.validate(snapshot, received_at=NOW)
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "BREADTH_COUNTS_DO_NOT_RECONCILE")

    def test_rejects_incompatible_schema_and_nan(self):
        snapshot = valid_snapshot()
        snapshot["api_schema_version"] = "umil-api-v99"
        self.assertFalse(self.validator.validate(snapshot, received_at=NOW).accepted)
        snapshot = valid_snapshot()
        snapshot["quality"]["completeness"] = float("nan")
        self.assertFalse(self.validator.validate(snapshot, received_at=NOW).accepted)


if __name__ == "__main__":
    unittest.main()
