from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import os
import tempfile
import unittest

from trading_assistant.strategy_manifest import (
    StrategyManifest,
    save_manifest,
)
from trading_assistant.strategy_registry import (
    StrategyConflictError,
    StrategyManifestRegistry,
    StrategyNotFoundError,
    StrategyNotReadyError,
    StrategyRegistryIntegrityError,
)


class StrategyManifestRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = (
            tempfile.TemporaryDirectory()
        )
        self.root = Path(
            self.temporary_directory.name
        )

        self.manifest_root = (
            self.root / "manifests"
        )
        self.artifact_root = (
            self.root / "artifacts"
        )

        self.manifest_root.mkdir()
        self.artifact_root.mkdir()

        self.artifact = (
            self.artifact_root
            / "btc"
            / "btc_usdt_5m.joblib"
        )
        self.artifact.parent.mkdir()
        self.artifact.write_bytes(
            b"verified-btc-5m-artifact"
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def payload(
        self,
        *,
        horizon: str = "5m",
        status: str = "active",
        version: str = "btc-5m-v1",
        promoted_at: str = "2026-07-21T12:00:00Z",
        artifact_path: str = (
            "btc/btc_usdt_5m.joblib"
        ),
    ) -> dict:
        return {
            "schema_version": 1,
            "symbol": "BTC/USDT",
            "horizon": horizon,
            "strategy_name": (
                "btc_direction_ensemble"
            ),
            "strategy_kind": "ml",
            "strategy_version": version,
            "artifact_path": artifact_path,
            "artifact_sha256": sha256(
                self.artifact.read_bytes()
            ).hexdigest(),
            "feature_schema_sha256": sha256(
                b"feature-schema-v1"
            ).hexdigest(),
            "training_timeframe": horizon,
            "prediction_horizon_bars": 1,
            "training_data_cutoff": (
                "2026-07-21T11:55:00Z"
            ),
            "promoted_at": promoted_at,
            "source_commit": "b" * 40,
            "promotion_metrics": {
                "num_folds": 8,
                "median_sharpe": 1.20,
                "pct_folds_profitable": 0.75,
            },
            "decision_thresholds": {
                "up": 0.60,
                "down": 0.40,
            },
            "status": status,
        }

    def write_manifest(
        self,
        name: str,
        **overrides,
    ) -> StrategyManifest:
        manifest = StrategyManifest.from_dict(
            self.payload(**overrides)
        )

        save_manifest(
            manifest,
            self.manifest_root / name,
        )

        return manifest

    def registry(self) -> StrategyManifestRegistry:
        return StrategyManifestRegistry(
            manifest_root=self.manifest_root,
            artifact_root=self.artifact_root,
        )

    def test_resolves_exact_active_symbol_and_horizon(
        self,
    ) -> None:
        expected = self.write_manifest(
            "btc_5m.json"
        )

        resolved = self.registry().resolve(
            "btc/usdt",
            "5m",
            as_of=datetime(
                2026,
                7,
                21,
                12,
                1,
                tzinfo=timezone.utc,
            ),
        )

        self.assertEqual(
            resolved.manifest,
            expected,
        )
        self.assertEqual(
            resolved.artifact_path,
            self.artifact,
        )
        self.assertEqual(
            len(resolved.manifest_sha256),
            64,
        )

    def test_wrong_horizon_is_not_resolved(
        self,
    ) -> None:
        self.write_manifest(
            "btc_5m.json"
        )

        with self.assertRaises(
            StrategyNotFoundError
        ):
            self.registry().resolve(
                "BTC/USDT",
                "15m",
            )

    def test_suspended_manifest_is_not_resolved(
        self,
    ) -> None:
        self.write_manifest(
            "btc_5m.json",
            status="suspended",
        )

        with self.assertRaises(
            StrategyNotFoundError
        ):
            self.registry().resolve(
                "BTC/USDT",
                "5m",
            )

    def test_future_promotion_is_not_eligible(
        self,
    ) -> None:
        self.write_manifest(
            "btc_5m.json",
            promoted_at="2026-07-22T12:00:00Z",
        )

        with self.assertRaises(
            StrategyNotReadyError
        ):
            self.registry().resolve(
                "BTC/USDT",
                "5m",
                as_of=datetime(
                    2026,
                    7,
                    21,
                    12,
                    0,
                    tzinfo=timezone.utc,
                ),
            )

    def test_duplicate_active_contracts_fail_closed(
        self,
    ) -> None:
        self.write_manifest(
            "btc_5m_v1.json",
            version="btc-5m-v1",
        )
        self.write_manifest(
            "btc_5m_v2.json",
            version="btc-5m-v2",
        )

        with self.assertRaises(
            StrategyConflictError
        ):
            self.registry().resolve(
                "BTC/USDT",
                "5m",
            )

    def test_tampered_artifact_is_rejected(
        self,
    ) -> None:
        self.write_manifest(
            "btc_5m.json"
        )

        self.artifact.write_bytes(
            b"tampered-after-promotion"
        )

        with self.assertRaises(
            StrategyRegistryIntegrityError
        ):
            self.registry().resolve(
                "BTC/USDT",
                "5m",
            )

    def test_symlinked_manifest_is_rejected(
        self,
    ) -> None:
        outside = (
            self.root / "outside_manifest.json"
        )

        manifest = StrategyManifest.from_dict(
            self.payload()
        )

        save_manifest(
            manifest,
            outside,
        )

        link = (
            self.manifest_root
            / "linked_manifest.json"
        )

        try:
            os.symlink(
                outside,
                link,
            )
        except OSError as exc:
            self.skipTest(
                f"symlinks unavailable: {exc}"
            )

        with self.assertRaises(
            StrategyRegistryIntegrityError
        ):
            self.registry().list_manifests()

    def test_symlinked_artifact_is_rejected(
        self,
    ) -> None:
        expected_digest = sha256(
            self.artifact.read_bytes()
        ).hexdigest()

        target = (
            self.artifact_root
            / "btc"
            / "real_model.joblib"
        )
        target.write_bytes(
            self.artifact.read_bytes()
        )

        self.artifact.unlink()

        try:
            os.symlink(
                target.name,
                self.artifact,
            )
        except OSError as exc:
            self.skipTest(
                f"symlinks unavailable: {exc}"
            )

        payload = self.payload()
        payload["artifact_sha256"] = expected_digest

        manifest = StrategyManifest.from_dict(
            payload
        )

        save_manifest(
            manifest,
            self.manifest_root / "btc_5m.json",
        )

        with self.assertRaises(
            StrategyRegistryIntegrityError
        ):
            self.registry().resolve(
                "BTC/USDT",
                "5m",
            )


if __name__ == "__main__":
    unittest.main()
