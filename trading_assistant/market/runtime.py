"""Production bootstrap for the broker market universe."""

from __future__ import annotations

import logging
from typing import Any, Iterable, List

from trading_assistant.market.universe import MarketUniverse

logger = logging.getLogger(__name__)


def _open_order_symbols(exchange: Any, universe: MarketUniverse) -> List[str]:
    try:
        # Binance allows an account-wide open-order snapshot but ccxt requires
        # explicit acknowledgement because it has a higher request weight.
        # This is one startup reconciliation call, not a polling path.
        options = getattr(exchange, "options", None)
        if isinstance(options, dict):
            options["warnOnFetchOpenOrdersWithoutSymbol"] = False

        orders = exchange.fetch_open_orders()
    except Exception:
        logger.exception("unable to bootstrap tracked markets from open orders")
        return []

    symbols = set()
    for order in orders or []:
        symbol = order.get("symbol") if isinstance(order, dict) else None
        if symbol and universe.catalog.get(symbol) is not None:
            symbols.add(symbol)

    return sorted(symbols)


def _holding_symbols(
    exchange: Any,
    universe: MarketUniverse,
    quote_currency: str,
) -> List[str]:
    try:
        balance = exchange.fetch_balance()
    except Exception:
        logger.exception("unable to bootstrap tracked markets from balances")
        return []

    total = balance.get("total") or {}
    quote = quote_currency.upper()

    by_base = {
        market.base.upper(): market.symbol
        for market in universe.catalog.search(quote=quote)
    }

    symbols = set()

    for asset, raw_quantity in total.items():
        try:
            quantity = float(raw_quantity or 0)
        except (TypeError, ValueError):
            continue

        asset = str(asset).upper()

        if quantity <= 0 or asset == quote:
            continue

        symbol = by_base.get(asset)
        if symbol:
            symbols.add(symbol)

    return sorted(symbols)


def bootstrap_market_universe(
    exchange: Any,
    configured_symbols: Iterable[str],
    *,
    quote_currency: str = "USDT",
) -> MarketUniverse:
    universe = MarketUniverse()

    active_count = universe.refresh(exchange)

    configured = [
        symbol
        for symbol in configured_symbols
        if universe.catalog.get(symbol) is not None
    ]

    holdings = _holding_symbols(exchange, universe, quote_currency)
    open_orders = _open_order_symbols(exchange, universe)

    universe.bootstrap(
        configured_symbols=configured,
        account_holding_symbols=holdings,
        open_order_symbols=open_orders,
    )

    logger.info(
        "market universe initialized",
        extra={
            "context": {
                "active_spot_markets": active_count,
                "configured": len(configured),
                "holdings": len(holdings),
                "open_orders": len(open_orders),
                "tracked": len(universe.tracked.symbols()),
            }
        },
    )

    return universe
