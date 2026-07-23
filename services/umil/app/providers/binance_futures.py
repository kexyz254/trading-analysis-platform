"""Credential-free Binance USD-margined derivatives context adapter."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .base import ProviderError


class BinanceFuturesPublicProvider:
    name = "binance_usdm"
    base_url = "https://fapi.binance.com"

    def __init__(self, *, timeout_seconds: float = 10.0) -> None:
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _symbol(value: str) -> str:
        symbol = value.strip().upper()
        if not symbol.isalnum() or not 3 <= len(symbol) <= 30:
            raise ValueError("invalid futures symbol")
        return symbol

    def _get(self, path: str, params: dict[str, Any]) -> Any:
        if not path.startswith("/fapi/v1/"):
            raise ValueError("futures provider only permits public v1 market paths")
        url = self.base_url + path + "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(
            url, headers={"Accept": "application/json", "User-Agent": "ATS-UMIL/1.5.5"}
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = response.read(8 * 1024 * 1024 + 1)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProviderError(self.name, path, type(exc).__name__) from exc
        if len(payload) > 8 * 1024 * 1024:
            raise ProviderError(self.name, path, "response exceeded safety limit")
        try:
            return json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ProviderError(self.name, path, "invalid JSON") from exc

    def fetch_context(self, symbol: str) -> dict[str, Any]:
        token = self._symbol(symbol)
        premium = self._get("/fapi/v1/premiumIndex", {"symbol": token})
        interest = self._get("/fapi/v1/openInterest", {"symbol": token})
        if not isinstance(premium, dict) or not isinstance(interest, dict):
            raise ProviderError(self.name, "context", "unexpected schema")
        return {"premium": premium, "open_interest": interest}
