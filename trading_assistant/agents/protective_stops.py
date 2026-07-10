"""Real, exchange-side stop-loss protection.

Before this module existed, a real position had NO protective floor at all:
`ExecutionAgent._buy()` opened positions with no stop_loss, and
`PortfolioManager.check_stop_loss_take_profit()` - the only stop-loss logic
that existed anywhere in the codebase - was never called from production
code. A position could only close if the model happened to fire a SELL in
some future cycle; if it didn't (or the process crashed/restarted and
"forgot" what it held, per the same amnesia bug fixed elsewhere this
session), the position rode the full drawdown with nothing to stop it.

Even a correctly-wired *software* stop-loss (poll price each cycle, close if
breached) would still die exactly when it's needed most: during a crash,
restart, or any gap in the polling loop. The only real fix is a resting
STOP_LOSS_LIMIT order placed directly on the exchange immediately after
every real buy fill - broker-side protection that survives this process
being down entirely.

Binance spot (confirmed via `exchange.load_markets()` against testnet)
supports STOP_LOSS_LIMIT: a stop order that converts to a limit sell once
the trigger price is touched. The limit leg is set a small buffer below the
trigger so the resulting limit order is immediately marketable (a stop
order whose limit price sits exactly at the trigger can rest unfilled if
price gaps through it).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# The limit leg of the STOP_LOSS_LIMIT order is set this fraction below the
# trigger (stopPrice) so it converts to an immediately-marketable limit
# order once triggered, rather than potentially resting unfilled if price
# gaps straight through the trigger level.
STOP_LIMIT_BUFFER_PCT = 0.002

# Order type strings (as ccxt/Binance report them) that count as an existing
# protective stop when checking fetch_open_orders().
_STOP_ORDER_TYPES = {"STOP_LOSS_LIMIT", "STOP_LOSS", "STOP", "STOP_MARKET"}


def place_stop_at_price(exchange: Any, symbol: str, quantity: float, stop_price: float) -> Optional[str]:
    """Place a resting STOP_LOSS_LIMIT sell for `quantity` at an EXPLICIT
    stop_price - the lower-level primitive both place_protective_stop
    (entry-price-relative) and the trailing-stop updater (an absolute,
    previously-computed price) build on, so the actual order-placement
    logic exists in exactly one place. Returns the exchange order id, or
    None if no protective order could be placed (loudly logged - an
    unprotected position is a real risk event, not a routine failure to
    shrug off)."""
    if exchange is None or quantity <= 0 or stop_price <= 0:
        return None

    limit_price = stop_price * (1 - STOP_LIMIT_BUFFER_PCT)
    try:
        order = exchange.create_order(
            symbol, "STOP_LOSS_LIMIT", "sell", quantity, limit_price, {"stopPrice": stop_price, "timeInForce": "GTC"}
        )
        order_id = order.get("id") if isinstance(order, dict) else None
        logger.info(
            "protective stop-loss order placed",
            extra={"context": {"symbol": symbol, "stop_price": stop_price, "quantity": quantity, "order_id": order_id}},
        )
        return order_id
    except Exception:  # noqa: BLE001 - must not crash the caller, but must be loud: this position is now unprotected
        logger.exception(
            "failed to place protective stop-loss order - position is UNPROTECTED",
            extra={"context": {"symbol": symbol, "quantity": quantity, "stop_price": stop_price}},
        )
        return None


def replace_protective_stop(
    exchange: Any, symbol: str, old_order_id: Optional[str], quantity: float, new_stop_price: float
) -> Optional[str]:
    """Atomically replace a resting protective stop with a new one at
    new_stop_price, via the exchange's own cancel-replace primitive
    (Binance's POST /api/v3/order/cancelReplace, exposed through ccxt as
    edit_order) instead of two separate cancel-then-place calls from this
    process.

    This closes the exact gap that used to cause repeated
    "trailing_stop_failed" alerts AND leave positions genuinely
    unprotected forever: a client-side cancel-then-place has a real window
    where the OLD stop is already gone but the NEW one hasn't landed yet -
    if placement then fails (observed on testnet as a transient
    InsufficientFunds error), the position is left with no protection at
    all, and nothing ever retries since the caller's own "do I have a
    stop_order_id" check then always sees None.

    cancelReplaceMode='STOP_ON_FAILURE' is Binance's own guarantee that the
    OLD stop is only ever cancelled once the NEW one is confirmed placed -
    there is no in-between unprotected state for an exchange-side
    rejection (insufficient balance, bad price, etc). It cannot fully
    cover an ambiguous NETWORK failure (timeout/connection reset, where
    this process genuinely can't know which side of the atomic operation
    the exchange reached) - callers should keep treating their existing
    stop_order_id as still valid on any exception here, rather than
    discarding it, and rely on the existing per-cycle reconciliation
    (check_triggered_stop) to catch actual drift.

    Falls back to a plain placement if there's no old_order_id to replace
    (nothing resting yet)."""
    if exchange is None or quantity <= 0 or new_stop_price <= 0:
        return None
    if not old_order_id:
        return place_stop_at_price(exchange, symbol, quantity, new_stop_price)

    limit_price = new_stop_price * (1 - STOP_LIMIT_BUFFER_PCT)
    try:
        order = exchange.edit_order(
            old_order_id, symbol, "STOP_LOSS_LIMIT", "sell", quantity, limit_price,
            {"stopPrice": new_stop_price, "timeInForce": "GTC", "cancelReplaceMode": "STOP_ON_FAILURE"},
        )
        order_id = order.get("id") if isinstance(order, dict) else None
        logger.info(
            "protective stop-loss order atomically replaced",
            extra={
                "context": {
                    "symbol": symbol,
                    "old_order_id": old_order_id,
                    "new_stop_price": new_stop_price,
                    "quantity": quantity,
                    "new_order_id": order_id,
                }
            },
        )
        return order_id
    except Exception:  # noqa: BLE001 - must not crash the caller; STOP_ON_FAILURE means the
        # OLD stop is still resting in the common (exchange-rejected) case.
        logger.warning(
            "failed to replace protective stop - the existing stop should remain in place, unchanged",
            extra={"context": {"symbol": symbol, "old_order_id": old_order_id, "attempted_stop_price": new_stop_price}},
        )
        return None


def place_protective_stop(
    exchange: Any, symbol: str, quantity: float, entry_price: float, stop_loss_pct: float
) -> Optional[str]:
    """Place a resting STOP_LOSS_LIMIT sell for `quantity` at
    entry_price*(1-stop_loss_pct). See place_stop_at_price for the
    underlying order-placement mechanics."""
    if entry_price <= 0 or stop_loss_pct <= 0:
        return None
    stop_price = entry_price * (1 - stop_loss_pct)
    return place_stop_at_price(exchange, symbol, quantity, stop_price)


def cancel_protective_stop(exchange: Any, symbol: str, order_id: Optional[str]) -> None:
    """Cancel a resting protective stop before a normal sell fires - a
    resting stop holds `quantity` in reserve (not free balance), so a market
    sell for the same quantity would otherwise fail with insufficient
    balance. Silently no-ops if there's nothing to cancel or the order
    already triggered/filled (an expected race, not an error)."""
    if exchange is None or not order_id:
        return
    try:
        exchange.cancel_order(order_id, symbol)
    except Exception:  # noqa: BLE001
        logger.info(
            "could not cancel protective stop order (likely already triggered, filled, or gone)",
            extra={"context": {"symbol": symbol, "order_id": order_id}},
        )


def find_open_protective_stop(exchange: Any, symbol: str) -> Optional[str]:
    """Return the order id of an existing resting protective stop for
    `symbol`, or None if there isn't one - used both to avoid placing a
    duplicate during reconciliation, and to detect when a stop has
    triggered (its order id is no longer in the open-orders list)."""
    if exchange is None:
        return None
    try:
        open_orders = exchange.fetch_open_orders(symbol)
    except Exception:  # noqa: BLE001
        logger.exception("could not fetch open orders while checking for a protective stop", extra={"context": {"symbol": symbol}})
        return None
    for order in open_orders:
        order_type = str(order.get("type") or "").upper()
        if order_type in _STOP_ORDER_TYPES:
            return order.get("id")
    return None


def check_triggered_stop(exchange: Any, symbol: str, stop_order_id: Optional[str]) -> Optional[dict]:
    """Check whether a previously-placed protective stop has triggered
    (filled) since it was placed. Returns the closed order's details
    (including its actual fill price) if triggered, else None.

    This is essential, not optional: once a resting stop can actually fire,
    this process's in-memory portfolio has no way to know that happened
    unless something asks the exchange - without this check, the local
    portfolio would keep believing a position is still open after the
    exchange already closed it, corrupting capital/equity tracking and
    causing a future SELL attempt to fail with insufficient balance."""
    if exchange is None or not stop_order_id:
        return None
    try:
        order = exchange.fetch_order(stop_order_id, symbol)
    except Exception:  # noqa: BLE001
        logger.exception(
            "could not fetch protective stop order status", extra={"context": {"symbol": symbol, "order_id": stop_order_id}}
        )
        return None
    status = str(order.get("status") or "").lower()
    if status in ("closed", "filled"):
        return order
    return None
