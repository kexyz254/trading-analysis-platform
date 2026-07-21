from __future__ import annotations

import json
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path

from trading_assistant.strategy_manifest import (
    ManifestIntegrityError,
    ManifestStateError,
    ManifestValidationError,
    StrategyManifest,
    load_manifest,
    save_manifest,
)


class StrategyManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = (
            tempfile.TemporaryDirectory()
        )
        self.root = Path(
            self.temporary_directory.name
        )

        self.artifact = (
            self.root
            / "models"
            / "btc_usdt_5m.joblib"
        )

        self.artifact.parent.mkdir(
            parents=True
        )
        self.artifact.write_bytes(
            b"institutional-test-artifact-v1"
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def payload(self) -> dict:
        artifact_digest = sha256(
            self.artifact.read_bytes()
        ).hexdigest()

        feature_digest = sha256(
            b"close,rsi,atr_pct,volume_zscore"
        ).hexdigest()

        return {
            "schema_version": 1,
            "symbol": "BTC/USDT",
            "horizon": "5m",
            "strategy_name": "btc_direction_ensemble",
            "strategy_kind": "ml",
            "strategy_version": "btc-5m-20260721.1",
            "artifact_path": (
                "models/btc_usdt_5m.joblib"
            ),
            "artifact_sha256": artifact_digest,
            "feature_schema_sha256": feature_digest,
            "training_timeframe": "5m",
            "prediction_horizon_bars": 1,
            "training_data_cutoff": (
                "2026-07-21T11:55:00+00:00"
            ),
            "promoted_at": (
                "2026-07-21T12:00:00Z"
            ),
            "source_commit": "a" * 40,
            "promotion_metrics": {
                "walk_forward_folds": 8,
                "median_sharpe": 1.25,
                "pct_folds_profitable": 0.75,
            },
            "decision_thresholds": {
                "probability_up": 0.60,
                "probability_down": 0.40,
            },
            "status": "active",
        }

    def test_valid_manifest_round_trips_and_verifies_artifact(
        self,
    ) -> None:
        manifest = StrategyManifest.from_dict(
            self.payload()
        )

        manifest_path = (
            self.root
            / "manifests"
            / "btc_5m.json"
        )

        save_manifest(
            manifest,
            manifest_path,
        )

        loaded = load_manifest(
            manifest_path
        )

        self.assertEqual(
            loaded.to_dict(),
            manifest.to_dict(),
        )
        self.assertEqual(
            loaded.manifest_sha256(),
            manifest.manifest_sha256(),
        )
        self.assertEqual(
            loaded.assert_issuable(self.root),
            self.artifact,
        )

        encoded = json.loads(
            manifest.canonical_json()
        )

        self.assertEqual(
            encoded["training_data_cutoff"],
            "2026-07-21T11:55:00Z",
        )

    def test_artifact_tampering_is_detected(
        self,
    ) -> None:
        manifest = StrategyManifest.from_dict(
            self.payload()
        )

        self.artifact.write_bytes(
            b"tampered"
        )

        with self.assertRaises(
            ManifestIntegrityError
        ):
            manifest.verify_artifact(
                self.root
            )

    def test_horizon_contract_mismatch_is_rejected(
        self,
    ) -> None:
        for field, value in (
            ("training_timeframe", "15m"),
            ("prediction_horizon_bars", 4),
        ):
            with self.subTest(field=field):
                payload = self.payload()
                payload[field] = value

                with self.assertRaises(
                    ManifestValidationError
                ):
                    StrategyManifest.from_dict(
                        payload
                    )

    def test_unsafe_artifact_paths_are_rejected(
        self,
    ) -> None:
        for unsafe_path in (
            "../secrets/model.joblib",
            "/etc/passwd",
            r"models\escape.joblib",
        ):
            with self.subTest(path=unsafe_path):
                payload = self.payload()
                payload["artifact_path"] = (
                    unsafe_path
                )

                with self.assertRaises(
                    ManifestValidationError
                ):
                    StrategyManifest.from_dict(
                        payload
                    )

    def test_missing_artifact_is_rejected(
        self,
    ) -> None:
        manifest = StrategyManifest.from_dict(
            self.payload()
        )

        self.artifact.unlink()

        with self.assertRaises(
            ManifestIntegrityError
        ):
            manifest.assert_issuable(
                self.root
            )

    def test_suspended_manifest_cannot_issue(
        self,
    ) -> None:
        payload = self.payload()
        payload["status"] = "suspended"

        manifest = StrategyManifest.from_dict(
            payload
        )

        with self.assertRaises(
            ManifestStateError
        ):
            manifest.assert_issuable(
                self.root
            )

    def test_unknown_fields_are_rejected(
        self,
    ) -> None:
        payload = self.payload()
        payload["unreviewed_override"] = True

        with self.assertRaises(
            ManifestValidationError
        ):
            StrategyManifest.from_dict(
                payload
            )

    def test_naive_timestamps_are_rejected(
        self,
    ) -> None:
        payload = self.payload()
        payload["promoted_at"] = (
            "2026-07-21T12:00:00"
        )

        with self.assertRaises(
            ManifestValidationError
        ):
            StrategyManifest.from_dict(
                payload
            )

    def test_training_cutoff_after_promotion_is_rejected(
        self,
    ) -> None:
        payload = self.payload()
        payload["training_data_cutoff"] = (
            "2026-07-21T12:01:00Z"
        )

        with self.assertRaises(
            ManifestValidationError
        ):
            StrategyManifest.from_dict(
                payload
            )


if __name__ == "__main__":
    unittest.main()
