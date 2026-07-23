from __future__ import annotations

import math
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi import HTTPException

from webapp import umil_gateway as gateway


NOW = datetime.now(timezone.utc)
AUTHORITY = {
    "read_only": True,
    "can_trade": False,
    "can_cancel_orders": False,
    "can_access_trading_credentials": False,
    "can_train": False,
    "can_influence_ats": False,
    "can_modify_risk": False,
    "can_modify_tournament": False,
    "can_write_ats_storage": False,
}
DNA = {key: 50.0 for key in gateway._DNA_KEYS}


def iso(value: datetime = NOW) -> str:
    return value.isoformat()


def market_row() -> dict:
    return {
        "instrument_id": "binance:spot:BTCUSDT",
        "venue_symbol": "BTCUSDT",
        "last_price": 65000.0,
        "return_24h_pct": 1.25,
        "high_price_24h": 66000.0,
        "low_price_24h": 64000.0,
        "base_volume_24h": 2000.0,
        "quote_volume_24h": 1_000_000.0,
        "spread_bps": None,
        "trade_count_24h": None,
        "event_time": iso(),
        "coverage_tier": "FOCUS",
        "intelligence_state": "READY",
        "dna": DNA,
        "universe_state": {
            "state": "READY",
            "as_of": iso(),
            "trend": "ADVANCING",
            "return_24h_pct": 1.25,
            "return_percentile": 70.0,
            "range_position": 0.7,
            "intraday_range_pct": 2.0,
            "volume_percentile": 80.0,
            "spread_bps": None,
            "anomaly_score": 72.0,
            "quality_flags": ["SPREAD_UNAVAILABLE"],
            "available_dimensions": ["trend"],
            "missing_dimensions": ["pressure"],
            "directional_probability": None,
        },
        "market_scale": None,
        "future_documented_field": {"kept": True},
    }


class UmilGatewayTest(unittest.TestCase):
    def setUp(self) -> None:
        with gateway._cache_lock:
            gateway._cache.clear()

    def test_base_url_is_loopback_only(self):
        with patch.dict(os.environ, {"ATS_UMIL_BASE_URL": "https://example.com:8100"}):
            with self.assertRaises(ValueError):
                gateway._base_url()

    def test_market_rows_preserve_complete_sanctioned_payload(self):
        payload = {
            "api_schema_version": gateway._SCHEMA,
            "as_of": iso(),
            "total": 1,
            "offset": 0,
            "limit": 1,
            "authority": AUTHORITY,
            "items": [market_row()],
        }
        safe = gateway._market_rows(payload)
        row = safe["items"][0]
        self.assertEqual(row["venue_symbol"], "BTCUSDT")
        self.assertEqual(row["universe_state"]["quality_flags"], ["SPREAD_UNAVAILABLE"])
        self.assertEqual(row["future_documented_field"], {"kept": True})
        self.assertEqual(safe["authority"]["can_trade"], False)

    def test_stale_anomaly_is_valid_display_data(self):
        stale = NOW - timedelta(minutes=12)
        payload = {
            "api_schema_version": gateway._SCHEMA,
            "authority": AUTHORITY,
            "items": [{
                "instrument_id": "binance:spot:BTCUSDT",
                "as_of": iso(stale),
                "anomaly_score": 90.0,
                "coverage": "FULL_DNA",
                "universe_state": {
                    "state": "STALE", "as_of": iso(stale), "trend": "ADVANCING",
                    "quality_flags": ["STALE_TICKER"],
                },
                "dna": DNA,
                "evidence_record_id": "11111111-1111-4111-8111-111111111111",
            }],
        }
        safe = gateway._anomalies(payload)
        row = safe["items"][0]
        self.assertEqual(row["display_freshness"], "STALE")
        self.assertEqual(row["universe_state"]["state"], "STALE")
        self.assertEqual(row["evidence_record_id"], "11111111-1111-4111-8111-111111111111")

    def test_invalid_anomaly_row_is_counted_without_hiding_valid_rows(self):
        valid = {
            "instrument_id": "binance:spot:BTCUSDT", "as_of": iso(),
            "anomaly_score": 80.0, "coverage": "UNIVERSE",
            "universe_state": {"state": "READY", "as_of": iso()},
            "dna": None, "evidence_record_id": None,
        }
        payload = {
            "api_schema_version": gateway._SCHEMA,
            "authority": AUTHORITY,
            "items": [valid, {"instrument_id": "bad"}],
        }
        safe = gateway._anomalies(payload)
        self.assertEqual(len(safe["items"]), 1)
        self.assertEqual(safe["gateway_rejected_rows"], 1)

    def test_authority_escalation_is_rejected(self):
        with self.assertRaises(ValueError):
            gateway._authority({"read_only": True, "can_influence_ats": True})

    def test_breadth_counts_must_reconcile(self):
        payload = {
            "api_schema_version": gateway._SCHEMA,
            "as_of": iso(), "authority": AUTHORITY,
            "eligible_instruments": 3, "observed_instruments": 3,
            "advancers": 2, "decliners": 2, "unchanged": 0,
            "advance_ratio": 0.5, "volume_weighted_advance_ratio": 0.5,
        }
        with self.assertRaises(ValueError):
            gateway._breadth(payload)

    def test_intelligence_preserves_dimensions_timeframes_and_evidence(self):
        payload = {
            "instrument_id": "binance:spot:BTCUSDT", "as_of": iso(),
            "state": "READY", "dna": DNA, "authority": AUTHORITY,
            "dimensions": {"TRN": {"score": 50.0, "inputs": ["return_6_pct"]}},
            "timeframes": {"15m": {"interval": "15m", "as_of": iso()}},
            "versions": {"service_version": "1.6.0"},
            "commentary": {"summary": "read only", "claims": []},
            "evidence_record_id": "11111111-1111-4111-8111-111111111111",
            "watch_conditions": [{"state": "ACTIVE"}],
        }
        safe = gateway._intelligence(payload)
        self.assertEqual(safe["dimensions"]["TRN"]["inputs"], ["return_6_pct"])
        self.assertIn("15m", safe["timeframes"])
        self.assertEqual(safe["watch_conditions"][0]["state"], "ACTIVE")

    def test_non_finite_values_are_rejected_anywhere_in_tree(self):
        with self.assertRaises(ValueError):
            gateway._json_tree({"unsafe": math.inf})

    def test_last_valid_data_is_explicitly_stale_on_failure(self):
        with patch.object(gateway, "_read_json", return_value={"value": 7}):
            live = gateway._validated("test", "/fixed", lambda value: value)
        self.assertEqual(live["gateway_status"], "LIVE")

        with patch.object(gateway, "_read_json", side_effect=OSError("offline")):
            stale = gateway._validated("test", "/fixed", lambda value: value)
        self.assertEqual(stale["gateway_status"], "STALE")
        self.assertEqual(stale["data"], {"value": 7})

    def test_expected_upstream_not_found_is_preserved(self):
        error = gateway._UpstreamHTTPError(404, "evidence not found")
        with patch.object(gateway, "_read_json", side_effect=error):
            with self.assertRaises(HTTPException) as raised:
                gateway._validated("missing", "/fixed", lambda value: value)
        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(raised.exception.detail, "evidence not found")

    def test_all_sanctioned_gateway_routes_are_registered(self):
        paths = {route.path for route in gateway.router.routes}
        expected = {
            "/api/pro/umil/health", "/api/pro/umil/authority", "/api/pro/umil/universe",
            "/api/pro/umil/breadth", "/api/pro/umil/markets", "/api/pro/umil/anomalies",
            "/api/pro/umil/intelligence/{symbol}", "/api/pro/umil/dna/{symbol}",
            "/api/pro/umil/insights/{symbol}", "/api/pro/umil/scale",
            "/api/pro/umil/scale/{symbol}", "/api/pro/umil/history/{symbol}",
            "/api/pro/umil/evidence/{evidence_id}",
        }
        self.assertTrue(expected.issubset(paths))

    def test_future_timestamp_is_rejected(self):
        with patch.object(gateway, "_utc_now", return_value=NOW):
            with self.assertRaises(ValueError):
                gateway._timestamp(iso(NOW + timedelta(minutes=1)))


if __name__ == "__main__":
    unittest.main()
