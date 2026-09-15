"""Canonical catalog of active spot markets exposed by a ccxt-style broker."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


def _number(value: Any) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _filter(info: dict, kind: str, field: str) -> Optional[float]:
    for item in info.get("filters", []) or []:
        if item.get("filterType") == kind:
            return _number(item.get(field))
    return None


@dataclass(frozen=True)
class MarketInfo:
    symbol: str
    base: str
    quote: str
    status: str
    price_precision: Optional[float]
    amount_precision: Optional[float]
    tick_size: Optional[float]
    lot_step: Optional[float]
    min_amount: Optional[float]
    min_notional: Optional[float]
    order_types: Tuple[str, ...]


class MarketCatalog:
    def __init__(self) -> None:
        self._markets: Dict[str, MarketInfo] = {}

    def refresh(self, exchange: Any) -> int:
        raw = exchange.load_markets()
        markets: Dict[str, MarketInfo] = {}

        for symbol, market in raw.items():
            info = market.get("info") or {}
            is_spot = market.get("spot")
            if is_spot is None:
                is_spot = market.get("type") == "spot"

            status = str(info.get("status") or "TRADING")
            active = market.get("active") is not False

            if not is_spot or not active or status != "TRADING":
                continue

            limits = market.get("limits") or {}
            precision = market.get("precision") or {}

            markets[symbol] = MarketInfo(
                symbol=symbol,
                base=str(market.get("base") or ""),
                quote=str(market.get("quote") or ""),
                status=status,
                price_precision=_number(precision.get("price")),
                amount_precision=_number(precision.get("amount")),
                tick_size=_filter(info, "PRICE_FILTER", "tickSize"),
                lot_step=_filter(info, "LOT_SIZE", "stepSize"),
                min_amount=_number((limits.get("amount") or {}).get("min")),
                min_notional=_number((limits.get("cost") or {}).get("min")),
                order_types=tuple(info.get("orderTypes") or ()),
            )

        self._markets = markets
        return len(markets)

    def get(self, symbol: str) -> Optional[MarketInfo]:
        return self._markets.get(symbol)

    def symbols(self) -> List[str]:
        return sorted(self._markets)

    def search(self, query: str = "", quote: Optional[str] = None) -> List[MarketInfo]:
        query = query.strip().upper()
        result = []
        for market in self._markets.values():
            if quote and market.quote.upper() != quote.upper():
                continue
            haystack = f"{market.symbol} {market.base} {market.quote}".upper()
            if query and query not in haystack:
                continue
            result.append(market)
        return sorted(result, key=lambda m: m.symbol)
