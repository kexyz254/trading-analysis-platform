from __future__ import annotations

import unittest
import sys
import types
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

# Load only the feature-engineering submodule under test.  The production
# package's convenience __init__ eagerly imports exchange adapters that are
# unrelated to these read-only forecast tests.
ROOT = Path(__file__).resolve().parents[1]
_STUB_KEYS = (
    "trading_assistant",
    "trading_assistant.agents",
    "trading_assistant.config",
    "webapp.data_access",
)
_SAVED_MODULES = {key: sys.modules.get(key) for key in _STUB_KEYS}
trading_package = types.ModuleType("trading_assistant")
trading_package.__path__ = [str(ROOT / "trading_assistant")]
agents_package = types.ModuleType("trading_assistant.agents")
agents_package.__path__ = [str(ROOT / "trading_assistant" / "agents")]
config_module = types.ModuleType("trading_assistant.config")
config_module.Config = object
sys.modules["trading_assistant"] = trading_package
sys.modules["trading_assistant.agents"] = agents_package
sys.modules["trading_assistant.config"] = config_module

import webapp

data_access_module = types.ModuleType("webapp.data_access")
data_access_module.fetch_ohlcv = lambda *_args, **_kwargs: []
sys.modules["webapp.data_access"] = data_access_module
setattr(webapp, "data_access", data_access_module)

from webapp import forecast

for _key, _module in _SAVED_MODULES.items():
    if _module is None:
        sys.modules.pop(_key, None)
    else:
        sys.modules[_key] = _module
if _SAVED_MODULES["webapp.data_access"] is not None:
    setattr(webapp, "data_access", _SAVED_MODULES["webapp.data_access"])


class ForecastLearningTest(unittest.TestCase):
    def setUp(self):
        with forecast._cache_lock:
            forecast._cache.clear()
            forecast._frame_cache.clear()

    @staticmethod
    def featured_frame(rows: int = 340) -> pd.DataFrame:
        index = np.arange(rows, dtype=float)
        close = 100 + 0.02 * index + np.sin(index / 2.7)
        data = {
            name: np.sin(index / (position + 2)) + position * 0.001
            for position, name in enumerate(forecast.MODEL_FEATURES)
        }
        data["close"] = close
        data["timestamp"] = pd.date_range(
            "2026-01-01", periods=rows, freq="5min", tz="UTC"
        )
        return pd.DataFrame(data)

    def test_open_exchange_candle_is_never_a_training_or_inference_row(self):
        raw = [
            {
                "timestamp": 1_800_000_000_000 + i * 300_000,
                "open": 100 + i,
                "high": 101 + i,
                "low": 99 + i,
                "close": 100.5 + i,
                "volume": 10,
            }
            for i in range(5)
        ]
        with patch.object(forecast.da, "fetch_ohlcv", return_value=raw):
            first = forecast._closed_bars_frame(None, "BTC/USDT", "5m")
        raw[-1]["close"] = 999_999
        with patch.object(forecast.da, "fetch_ohlcv", return_value=raw):
            second = forecast._closed_bars_frame(None, "BTC/USDT", "5m")
        self.assertEqual(len(first), 4)
        pd.testing.assert_frame_equal(first, second)

    def test_forecast_records_training_identity_inference_and_isolation(self):
        featured = self.featured_frame()
        source = featured[["timestamp", "close"]].copy()
        with (
            patch.object(forecast, "_closed_bars_frame", return_value=source),
            patch.object(forecast, "_featured_frame", return_value=featured),
        ):
            result = forecast.get_forecast(None, "BTC/USDT", "5m")

        self.assertEqual(len(result["model_fingerprint"]), 64)
        self.assertTrue(result["closed_candles_only"])
        self.assertFalse(result["execution_authority"])
        self.assertFalse(result["promotion_authority"])
        self.assertEqual(result["evidence_scope"], "DASHBOARD_ANALYTICS_ONLY")
        self.assertTrue(all(item["trained_rows"] >= 150 for item in result["horizons"]))
        self.assertTrue(all(item["holdout_bars"] >= 20 for item in result["horizons"]))

        health = forecast.get_forecast_health("BTC/USDT", "5m")
        self.assertEqual(health["state"], "READY")
        self.assertEqual(health["model_fingerprint"], result["model_fingerprint"])
        self.assertEqual(health["inference_count"], 1)
        self.assertIsNotNone(health["last_inference_at_ms"])
        self.assertEqual(len(health["horizons"]), 3)

        snapshot = forecast.get_latest_forecast_snapshot("BTC/USDT", "5m")
        self.assertEqual(snapshot, result)
        snapshot["horizons"][0]["direction"] = "TAMPERED"
        self.assertEqual(
            forecast.get_latest_forecast_snapshot("BTC/USDT", "5m")[
                "horizons"
            ][0]["direction"],
            result["horizons"][0]["direction"],
        )

    def test_health_probe_does_not_train_a_missing_forecast(self):
        health = forecast.get_forecast_health("BTC/USDT", "15m")
        self.assertEqual(health["state"], "NO_EVIDENCE")
        with forecast._cache_lock:
            self.assertEqual(forecast._cache, {})
        self.assertIsNone(
            forecast.get_latest_forecast_snapshot("BTC/USDT", "15m")
        )


if __name__ == "__main__":
    unittest.main()
