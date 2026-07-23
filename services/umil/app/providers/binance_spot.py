"""Credential-free Binance Spot reference-data and REST bootstrap adapter."""

from __future__ import annotations

import json
import urllib.parse
import urllib.error
import urllib.request
from typing import Any, Sequence

from .base import ProviderError


class BinanceSpotPublicProvider:
    name = "binance_spot"
    base_url = "https://api.binance.com"
    _max_response_bytes = 32 * 1024 * 1024

    def __init__(self, *, timeout_seconds: float = 10.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.timeout_seconds = timeout_seconds

    def _get_json(self, path: str) -> Any:
        if not path.startswith("/api/v3/"):
            raise ValueError("Binance provider only permits public market-data paths")
        request = urllib.request.Request(
            self.base_url + path,
            headers={"Accept": "application/json", "User-Agent": "ATS-UMIL/1.5.5"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                if response.status != 200:
                    raise ProviderError(self.name, path, f"HTTP {response.status}")
                payload = response.read(self._max_response_bytes + 1)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProviderError(self.name, path, type(exc).__name__) from exc
        if len(payload) > self._max_response_bytes:
            raise ProviderError(self.name, path, "response exceeded safety limit")
        try:
            return json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ProviderError(self.name, path, "invalid JSON") from exc

    def fetch_exchange_info(self) -> dict[str, Any]:
        payload = self._get_json("/api/v3/exchangeInfo")
        if not isinstance(payload, dict) or not isinstance(payload.get("symbols"), list):
            raise ProviderError(self.name, "exchangeInfo", "unexpected schema")
        return payload

    def fetch_all_tickers(self) -> Sequence[dict[str, Any]]:
        payload = self._get_json("/api/v3/ticker/24hr")
        if not isinstance(payload, list) or any(not isinstance(row, dict) for row in payload):
            raise ProviderError(self.name, "ticker/24hr", "unexpected schema")
        return payload

    @staticmethod
    def _symbol(value: str) -> str:
        symbol = value.strip().upper()
        if not symbol or not symbol.isalnum() or len(symbol) > 30:
            raise ValueError("invalid Binance symbol")
        return symbol

    def fetch_klines(
        self, symbol: str, interval: str, *, limit: int = 120,
    ) -> Sequence[Sequence[Any]]:
        if interval not in {"1m", "5m", "15m", "1h", "4h", "1d"}:
            raise ValueError("unsupported kline interval")
        if not 30 <= limit <= 1000:
            raise ValueError("kline limit must be in [30, 1000]")
        query = urllib.parse.urlencode({
            "symbol": self._symbol(symbol), "interval": interval, "limit": limit,
        })
        payload = self._get_json(f"/api/v3/klines?{query}")
        if not isinstance(payload, list) or any(not isinstance(row, list) for row in payload):
            raise ProviderError(self.name, "klines", "unexpected schema")
        return payload

    def fetch_aggregate_trades(
        self, symbol: str, *, limit: int = 1000,
    ) -> Sequence[dict[str, Any]]:
        if not 1 <= limit <= 1000:
            raise ValueError("trade limit must be in [1, 1000]")
        query = urllib.parse.urlencode({"symbol": self._symbol(symbol), "limit": limit})
        payload = self._get_json(f"/api/v3/aggTrades?{query}")
        if not isinstance(payload, list) or any(not isinstance(row, dict) for row in payload):
            raise ProviderError(self.name, "aggTrades", "unexpected schema")
        return payload

    def fetch_depth(self, symbol: str, *, limit: int = 100) -> dict[str, Any]:
        if limit not in {5, 10, 20, 50, 100, 500, 1000, 5000}:
            raise ValueError("unsupported depth limit")
        query = urllib.parse.urlencode({"symbol": self._symbol(symbol), "limit": limit})
        payload = self._get_json(f"/api/v3/depth?{query}")
        if not isinstance(payload, dict) or not isinstance(payload.get("bids"), list) or not isinstance(payload.get("asks"), list):
            raise ProviderError(self.name, "depth", "unexpected schema")
        return payload
