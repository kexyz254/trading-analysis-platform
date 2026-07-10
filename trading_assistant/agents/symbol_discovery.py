"""SymbolDiscoveryAgent: dynamically selects which Binance pairs the system
watches and trades, instead of a hand-picked static list.

Binance lists thousands of pairs; polling/training all of them would exceed
API rate limits within minutes and make retraining computationally
impossible on a single machine. This discovers the full market list,
filters to a liquid, tradeable subset (one quote currency, a minimum 24h
volume floor), and ranks by volume - "know about everything, focus on what
matters" rather than either a fixed hand-picked list or a naive attempt at
literally everything.
"""

from __future__ import annotations

import logging
from typing import Any, List

logger = logging.getLogger(__name__)


class SymbolDiscoveryAgent:
    def __init__(
        self,
        exchange: Any,
        quote_currency: str = "USDT",
        min_24h_volume_usd: float = 5_000_000.0,
        max_symbols: int = 20,
    ):
        self.exchange = exchange
        self.quote_currency = quote_currency
        self.min_24h_volume_usd = min_24h_volume_usd
        self.max_symbols = max_symbols

    def discover(self) -> List[str]:
        """Return up to max_symbols active spot symbols quoted in
        quote_currency, ranked by 24h quote volume descending."""
        markets = self.exchange.load_markets()
        tickers = self.exchange.fetch_tickers()

        candidates = []
        for symbol, market in markets.items():
            if not market.get("active", True):
                continue
            if market.get("quote") != self.quote_currency:
                continue
            if market.get("type") not in (None, "spot"):
                continue
            ticker = tickers.get(symbol)
            if not ticker:
                continue
            # A live bid AND ask means there's an actual resting order book
            # right now - trailing 24h quoteVolume alone isn't enough,
            # since it can reflect activity from earlier in the window on a
            # pair that's since gone quiet. Found via a real pair (COS/USDT
            # on testnet) that cleared the volume floor while reporting
            # bid=None/ask=None - a dead market, not a tradeable one - and
            # whose stale 24h stats also produced a nonsensical -78% "move".
            if ticker.get("bid") is None or ticker.get("ask") is None:
                continue
            if ticker["bid"] <= 0 or ticker["ask"] <= 0:
                continue
            volume = ticker.get("quoteVolume") or 0.0
            if volume < self.min_24h_volume_usd:
                continue
            candidates.append((symbol, volume))

        candidates.sort(key=lambda item: item[1], reverse=True)
        selected = [symbol for symbol, _ in candidates[: self.max_symbols]]
        logger.info(
            "symbol discovery complete",
            extra={
                "context": {
                    "candidates": len(candidates),
                    "selected": len(selected),
                    "quote_currency": self.quote_currency,
                    "min_24h_volume_usd": self.min_24h_volume_usd,
                }
            },
        )
        return selected
