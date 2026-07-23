from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import HTTPException

from services.umil.app.api import create_app
from services.umil.app.config import Settings


class _DegradedService:
    def health(self):
        return {
            "service": "umil",
            "service_version": "1.6.1",
            "state": "STALE",
            "last_snapshot_at": datetime(2026, 7, 23, 5, 0, tzinfo=timezone.utc),
            "started_at": datetime(2026, 7, 23, 4, 44, tzinfo=timezone.utc),
        }

    async def start(self):
        return None

    async def stop(self):
        return None


class ReadinessSerializationTest(unittest.IsolatedAsyncioTestCase):
    async def test_degraded_report_with_datetimes_remains_structured_503(self):
        with TemporaryDirectory() as directory:
            settings = Settings(
                database_path=Path(directory) / "umil.db",
                auto_start=False,
            )
            app = create_app(settings=settings, service=_DegradedService())
            endpoint = next(
                route.endpoint
                for route in app.routes
                if getattr(route, "path", None) == "/health/ready"
            )

            with self.assertRaises(HTTPException) as raised:
                await endpoint()

        error = raised.exception
        self.assertEqual(error.status_code, 503)
        self.assertEqual(error.detail["state"], "STALE")
        self.assertEqual(
            error.detail["last_snapshot_at"],
            "2026-07-23T05:00:00+00:00",
        )
        json.dumps({"detail": error.detail})


class PublicReadOnlySurfaceTest(unittest.TestCase):
    def test_all_sanctioned_public_routes_remain_registered(self):
        with TemporaryDirectory() as directory:
            settings = Settings(
                database_path=Path(directory) / "umil.db",
                auto_start=False,
            )
            app = create_app(settings=settings, service=_DegradedService())

        paths = {
            getattr(route, "path", None)
            for route in app.routes
        }
        expected = {
            "/health/live",
            "/health/ready",
            "/api/v1/authority",
            "/api/v1/market-universe",
            "/api/v1/market-intelligence",
            "/api/v1/market-intelligence/{symbol}",
            "/api/v1/market-dna/{symbol}",
            "/api/v1/market-insights/{symbol}",
            "/api/v1/market-anomalies",
            "/api/v1/market-scale/{symbol}",
            "/api/v1/market-scale",
            "/api/v1/historical/intelligence/{symbol}",
            "/api/v1/market-breadth",
            "/api/v1/evidence/{evidence_id}",
        }
        self.assertTrue(expected.issubset(paths))


if __name__ == "__main__":
    unittest.main()
