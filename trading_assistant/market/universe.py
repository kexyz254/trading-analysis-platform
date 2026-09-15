"""Live market-universe coordinator."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, Iterable, List, Optional

from trading_assistant.market.catalog import MarketCatalog
from trading_assistant.market.tracking import TrackedPairRegistry


class MarketUniverse:
    def __init__(
        self,
        catalog: Optional[MarketCatalog] = None,
        tracked: Optional[TrackedPairRegistry] = None,
    ) -> None:
        self.catalog = catalog or MarketCatalog()
        self.tracked = tracked or TrackedPairRegistry()

    def refresh(self, exchange: Any) -> int:
        return self.catalog.refresh(exchange)

    def bootstrap(
        self,
        configured_symbols: Iterable[str] = (),
        *,
        watchlist_symbols: Iterable[str] = (),
        open_position_symbols: Iterable[str] = (),
        open_order_symbols: Iterable[str] = (),
        account_holding_symbols: Iterable[str] = (),
    ) -> None:
        self.tracked.track_many(configured_symbols, "ats_candidate")
        self.tracked.track_many(watchlist_symbols, "watchlist")
        self.tracked.track_many(open_position_symbols, "manual_position")
        self.tracked.track_many(open_order_symbols, "open_order")
        self.tracked.track_many(account_holding_symbols, "account_holding")

    def select(self, symbol: str) -> None:
        if self.catalog.get(symbol) is None:
            raise ValueError(f"symbol is not an active supported market: {symbol}")
        self.tracked.track(symbol, "selected")

    def search(self, query: str = "", quote: Optional[str] = None) -> List[Dict]:
        return [asdict(m) for m in self.catalog.search(query=query, quote=quote)]

    def snapshot(self) -> Dict:
        return {
            "market_count": len(self.catalog.symbols()),
            "tracked_count": len(self.tracked.symbols()),
            "tracked": self.tracked.snapshot(),
        }
