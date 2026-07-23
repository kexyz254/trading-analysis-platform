from __future__ import annotations

import unittest
from datetime import timedelta

from services.umil.app.breadth import calculate_universe_breadth
from services.umil.app.contracts import OperationalState
from services.umil.app.universe import UniverseRegistry
from services.umil.tests.fixtures import EXCHANGE_INFO, NOW, REST_TICKERS, ticker


class UniverseAndBreadthTest(unittest.TestCase):
    def setUp(self):
        self.registry = UniverseRegistry(quote_assets=("USDT",))
        self.instruments = self.registry.from_binance_exchange_info(EXCHANGE_INFO)

    def test_universe_filters_status_permission_and_quote(self):
        self.assertEqual([row.venue_symbol for row in self.instruments], ["BTCUSDT", "ETHUSDT"])

    def test_universe_skips_unrepresentable_provider_symbol_without_aborting(self):
        payload = {
            "symbols": [
                *EXCHANGE_INFO["symbols"],
                {
                    "symbol": "币安人生USDT",
                    "status": "TRADING",
                    "baseAsset": "币安人生",
                    "quoteAsset": "USDT",
                    "permissions": ["SPOT"],
                    "quotePrecision": 8,
                    "baseAssetPrecision": 8,
                },
            ]
        }

        instruments = self.registry.from_binance_exchange_info(payload)

        self.assertEqual([row.venue_symbol for row in instruments], ["BTCUSDT", "ETHUSDT"])

    def test_rest_tickers_are_normalized_without_fabrication(self):
        rows = self.registry.normalize_rest_tickers(REST_TICKERS, self.instruments, received_at=NOW)
        self.assertEqual(len(rows), 2)
        self.assertAlmostEqual(rows[0].return_24h_pct, 2.0)
        self.assertIsNotNone(rows[0].spread_bps)

    def test_breadth_arithmetic_reconciles(self):
        rows = (
            ticker("AAAUSDT", 110, 100, 60),
            ticker("BBBUSDT", 90, 100, 30),
            ticker("CCCUSDT", 100, 100, 10),
        )
        result = calculate_universe_breadth(
            rows, eligible_instruments=3, as_of=NOW, provider_latency_ms={"feed": 10}
        )
        self.assertEqual((result.advancers, result.decliners, result.unchanged), (1, 1, 1))
        self.assertAlmostEqual(result.advance_ratio, 1 / 3)
        self.assertAlmostEqual(result.volume_weighted_advance_ratio, 0.6)
        self.assertAlmostEqual(result.volume_concentration_hhi, 0.46)
        self.assertEqual(result.state, OperationalState.READY)

    def test_stale_data_never_becomes_neutral_ready_data(self):
        rows = (ticker("AAAUSDT", 110, 100, 60, event_time=NOW - timedelta(seconds=30)),)
        result = calculate_universe_breadth(
            rows, eligible_instruments=1, as_of=NOW, stale_after_ms=15_000
        )
        self.assertEqual(result.state, OperationalState.STALE)
        self.assertEqual(result.observed_instruments, 0)
        self.assertIn("STALE_TICKERS", result.quality.quality_flags)

    def test_fresh_change_stream_projection_keeps_quiet_symbols_observed(self):
        rows = (
            ticker("AAAUSDT", 110, 100, 60, event_time=NOW - timedelta(hours=2)),
            ticker("BBBUSDT", 90, 100, 30, event_time=NOW - timedelta(minutes=5)),
        )
        result = calculate_universe_breadth(
            rows,
            eligible_instruments=2,
            as_of=NOW,
            projection_observed_at=NOW - timedelta(seconds=1),
            stale_after_ms=15_000,
        )
        self.assertEqual(result.state, OperationalState.READY)
        self.assertEqual(result.observed_instruments, 2)
        self.assertNotIn("STALE_TICKERS", result.quality.quality_flags)

    def test_stale_change_stream_projection_invalidates_whole_projection(self):
        rows = (ticker("AAAUSDT", 110, 100, 60),)
        result = calculate_universe_breadth(
            rows,
            eligible_instruments=1,
            as_of=NOW,
            projection_observed_at=NOW - timedelta(seconds=30),
            stale_after_ms=15_000,
        )
        self.assertEqual(result.state, OperationalState.STALE)
        self.assertEqual(result.observed_instruments, 0)
        self.assertIn("STALE_TICKERS", result.quality.quality_flags)

    def test_missing_provider_degrades_otherwise_complete_snapshot(self):
        rows = (ticker("AAAUSDT", 110, 100, 60),)
        result = calculate_universe_breadth(
            rows, eligible_instruments=1, as_of=NOW, missing_providers=("market_cap",)
        )
        self.assertEqual(result.state, OperationalState.DEGRADED)
        self.assertEqual(result.quality.missing_providers, ("market_cap",))


if __name__ == "__main__":
    unittest.main()
