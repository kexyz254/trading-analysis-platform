from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timezone
from pathlib import Path

from services.umil.app.config import Settings
from services.umil.app.service import UniverseIntelligenceService
from services.umil.tests.fixtures import EXCHANGE_INFO, REST_TICKERS


class FakeProvider:
    name = "fake_public_provider"

    def __init__(self):
        self.ticker_fetches = 0

    def fetch_exchange_info(self):
        return EXCHANGE_INFO

    def fetch_all_tickers(self):
        self.ticker_fetches += 1
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        return [{**row, "closeTime": now_ms} for row in REST_TICKERS]


class UniverseIntelligenceServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        settings = Settings(
            database_path=Path(self.temp.name) / "umil.db",
            auto_start=False,
        )
        self.service = UniverseIntelligenceService(settings, provider=FakeProvider())

    async def asyncTearDown(self):
        await self.service.stop()
        self.temp.cleanup()

    async def test_bootstrap_persists_universe_and_breadth_without_ats(self):
        await self.service.bootstrap()
        health = self.service.health()
        self.assertEqual(health["state"], "READY")
        self.assertEqual(health["eligible_instruments"], 2)
        self.assertEqual(health["observed_instruments"], 2)
        snapshot = self.service.latest_breadth()
        self.assertEqual(snapshot["observed_instruments"], 2)
        self.assertTrue(snapshot["authority"]["read_only"])
        self.assertFalse(snapshot["authority"]["can_trade"])
        self.assertTrue(snapshot["evidence_ids"])
        self.assertEqual(len(self.service.ledger.list_instruments()), 2)


    async def test_health_uses_cached_evidence_status_without_scanning_ledger(self):
        await self.service.bootstrap()
        with patch.object(
            self.service.ledger,
            "verify_chain",
            side_effect=AssertionError("readiness performed a chain scan"),
        ):
            health = self.service.health()
        self.assertEqual(health["state"], "READY")
        self.assertTrue(health["evidence_chain_valid"])
        self.assertEqual(health["evidence_chain_state"], "VALID")

    async def test_unverified_existing_ledger_degrades_without_blocking_health(self):
        await self.service.bootstrap()
        reopened = UniverseIntelligenceService(
            self.service.settings, provider=FakeProvider()
        )
        try:
            reopened._instruments = self.service._instruments
            reopened.ticker_book = self.service.ticker_book
            reopened._provider_state = "LIVE"
            health = reopened.health()
            self.assertEqual(health["state"], "DEGRADED")
            self.assertIsNone(health["evidence_chain_valid"])
            self.assertEqual(health["evidence_chain_state"], "UNVERIFIED")
        finally:
            await reopened.stop()

    async def test_stream_recovery_reconciles_complete_ticker_projection(self):
        await self.service.bootstrap()
        self.assertEqual(self.service.provider.ticker_fetches, 1)

        await self.service._on_stream_state("LIVE", None)
        self.assertEqual(self.service.provider.ticker_fetches, 1)

        await self.service._on_stream_state("RECOVERING", "ConnectionClosed")
        await self.service._on_stream_state("LIVE", None)

        self.assertEqual(self.service.provider.ticker_fetches, 2)
        self.assertEqual(self.service.health()["provider_state"], "LIVE")
        self.assertEqual(
            self.service.health()["sequence_gap_recoveries_since_start"], 1
        )


if __name__ == "__main__":
    unittest.main()
