"""Optional public supply/market-cap provider; never used for exchange flow."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Sequence

from .base import ProviderError


class CoinGeckoPublicProvider:
    name = "coingecko"
    base_url = "https://api.coingecko.com/api/v3"

    def __init__(self, *, timeout_seconds: float = 15.0) -> None:
        self.timeout_seconds = timeout_seconds

    def fetch_markets(self, *, pages: int = 2) -> Sequence[dict[str, Any]]:
        if not 1 <= pages <= 4:
            raise ValueError("pages must be in [1, 4]")
        output: list[dict[str, Any]] = []
        for page in range(1, pages + 1):
            query = urllib.parse.urlencode({
                "vs_currency": "usd", "order": "market_cap_desc",
                "per_page": 250, "page": page, "sparkline": "false",
                "price_change_percentage": "24h",
            })
            request = urllib.request.Request(
                f"{self.base_url}/coins/markets?{query}",
                headers={"Accept": "application/json", "User-Agent": "ATS-UMIL/1.5.5"},
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    payload = response.read(16 * 1024 * 1024 + 1)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                raise ProviderError(self.name, "coins/markets", type(exc).__name__) from exc
            if len(payload) > 16 * 1024 * 1024:
                raise ProviderError(self.name, "coins/markets", "response exceeded safety limit")
            try:
                rows = json.loads(payload)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ProviderError(self.name, "coins/markets", "invalid JSON") from exc
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise ProviderError(self.name, "coins/markets", "unexpected schema")
            output.extend(rows)
        return output
