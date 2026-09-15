"""ExecutionAgent: places orders for multiple symbols via a ccxt-style exchange, with retry/backoff."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from trading_assistant.agents.protective_stops import cancel_protective_stop, place_protective_stop, place_stop_at_price
from trading_assistant.execution.order_type import choose_order_type, marketable_limit_price
from trading_assistant.execution.quality import compute_slippage_pct
from trading_assistant.portfolio.manager import PortfolioManager
from trading_assistant.risk.execution_quality import spread_pct, spread_too_wide
from trading_assistant.strategies.base import Signal

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    symbol: str
    action: str  # "opened_position" | "closed_position" | "no_action" | "error"
    price: float
    quantity: float
    pnl: Optional[float] = None
    error: Optional[str] = None
    # Execution-quality telemetry - None for "no_action"/"error" outcomes
    # that never actually reached the exchange, or when there was no
    # exchange to measure against (paper/testnet-only runs with no real
    # order). See trading_assistant.execution.quality for the sign
    # convention (positive = worse than intended, for either side).
    slippage_pct: Optional[float] = None
    latency_ms: Optional[float] = None
    order_type: Optional[str] = None
    # None: no protective stop was attempted (no exchange, no stop_loss_pct
    # configured, or this action doesn't leave an open position at all).
    # True: a resting protective stop is confirmed in place. False: one was
    # ATTEMPTED and failed - the position is currently open on the real
    # exchange with no protective floor. The caller (Orchestrator, which
    # owns alert_manager - ExecutionAgent deliberately doesn't) is
    # responsible for surfacing False as a loud alert, not silently logging.
    stop_order_placed: Optional[bool] = None


class ExecutionAgent:
    """Places orders against a ccxt-style exchange and keeps the in-memory portfolio in sync.

    Fixes a bug in LiveTradingEngine.run_once: its sell path only closed the
    in-memory PortfolioManager position and never called
    exchange.create_market_sell_order, which would leave a real exchange
    position open. It also has no retry handling, so a transient
    network/rate-limit error from ccxt would kill the whole loop.
    """

    def __init__(
        self,
        portfolio: PortfolioManager,
        exchange: Any = None,
        max_retries: int = 3,
        backoff_seconds: float = 1.0,
        stop_loss_pct: Optional[float] = None,
        max_spread_pct: Optional[float] = None,
        # Adaptive order placement: a marketable LIMIT order (capped
        # price) instead of an unbounded MARKET order when the spread is
        # comfortably tight - see trading_assistant.execution.order_type.
        # False (default) always uses market orders, backward compatible.
        enable_adaptive_order_type: bool = False,
        execution_mode: str = "autonomous",
    ):
        self.portfolio = portfolio
        self.exchange = exchange
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        # A real position gets NO protective floor at all unless this is
        # set: a resting STOP_LOSS_LIMIT sell is placed on the exchange
        # immediately after every real buy fill (see protective_stops.py).
        # None (the default) disables it - callers running against a real
        # exchange should always pass the same stop_loss_pct RiskAgent uses
        # for position sizing, so the assumed stop distance is the real one.
        self.stop_loss_pct = stop_loss_pct
        # A market order fired during a moment of abnormally wide bid-ask
        # spread can fill far worse than the price risk-sizing assumed -
        # None disables this pre-trade check (opt-in, backward compatible).
        self.max_spread_pct = max_spread_pct
        self.enable_adaptive_order_type = enable_adaptive_order_type
        self.execution_mode = execution_mode

    def execute(
        self, symbol: str, signal: Signal, price: float, quantity: float, stop_loss_pct: Optional[float] = None
    ) -> ExecutionResult:
        if self.execution_mode != "autonomous":
            return ExecutionResult(
                symbol=symbol,
                action="no_action",
                price=price,
                quantity=0.0,
                error="autonomous execution disabled: manual_assist",
            )
        if signal == Signal.BUY and symbol not in self.portfolio.positions:
            return self._buy(symbol, price, quantity, stop_loss_pct)
        if signal == Signal.SELL and symbol in self.portfolio.positions:
            return self._sell(symbol, price)
        return ExecutionResult(symbol=symbol, action="no_action", price=price, quantity=0.0)

    def _buy(self, symbol: str, price: float, quantity: float, stop_loss_pct: Optional[float] = None) -> ExecutionResult:
        if quantity <= 0 or price <= 0:
            return ExecutionResult(symbol=symbol, action="no_action", price=price, quantity=0.0)

        # Check affordability BEFORE placing the real exchange order - placing
        # the order first and only then discovering the portfolio can't
        # afford it would leave a real, filled exchange position with no
        # local record and no capital deducted.
        if price * quantity > self.portfolio.capital:
            return ExecutionResult(
                symbol=symbol, action="no_action", price=price, quantity=0.0, error="insufficient capital"
            )

        # Defensive final check against the exchange's REAL minimum order
        # value - RiskAgent.evaluate_buy already sizes against this (see
        # its exchange_min_notional param), but that lookup can fail or be
        # stale between evaluation and execution. Without this, an
        # under-sized order would be sent to the real exchange, get
        # rejected, and count as a genuine "error" against the circuit
        # breaker's budget for something that was never going to work -
        # not a real execution failure, just a sizing mismatch this system
        # already knew how to avoid. no_action (not "error"): this must
        # never burn circuit-breaker budget.
        if self.exchange is not None:
            try:
                market = self.exchange.market(symbol)
                min_notional = market.get("limits", {}).get("cost", {}).get("min")
            except Exception:  # noqa: BLE001 - a failed lookup must not block a trade RiskAgent already approved
                min_notional = None

            # The exchange rounds the executed quantity DOWN to its lot-size
            # precision before filling - a quantity whose PRE-rounding
            # notional exactly equals the minimum can round down to a real
            # notional below it (e.g. BNB/USDT's 0.001 lot step is worth
            # ~$0.57 at ~$574/BNB, enough to shave a $5.00-exact order down
            # to $4.59 and get rejected by Binance's own NOTIONAL filter -
            # this happened for real and tripped the circuit breaker on a
            # sizing mismatch, not a genuine execution failure). Round to
            # precision here (the raw quantity was never precision-rounded
            # before reaching the exchange at all) and, if that shaves the
            # notional below the minimum, nudge the pre-rounding quantity up
            # until the ROUNDED notional actually clears it.
            try:
                precise_quantity = float(self.exchange.amount_to_precision(symbol, quantity))
                if min_notional is not None and price * precise_quantity < min_notional:
                    for bump in (1.002, 1.01, 1.05, 1.1, 1.25, 1.5):
                        candidate = float(self.exchange.amount_to_precision(symbol, quantity * bump))
                        if price * candidate >= min_notional:
                            precise_quantity = candidate
                            break
                    else:
                        precise_quantity = candidate
                quantity = precise_quantity
            except Exception:  # noqa: BLE001 - a failed precision lookup must not block a trade RiskAgent already approved
                pass

            if min_notional is not None and price * quantity < min_notional:
                logger.warning(
                    "rejecting buy - order value is below the exchange's real minimum for this symbol",
                    extra={
                        "context": {
                            "symbol": symbol, "order_value": price * quantity, "exchange_min_notional": min_notional,
                        }
                    },
                )
                return ExecutionResult(
                    symbol=symbol, action="no_action", price=price, quantity=0.0,
                    error=f"order value ${price * quantity:.2f} below exchange minimum ${min_notional:.2f}",
                )

            # Re-verify affordability after any precision-driven bump - the
            # bump is small (RiskAgent's stake floor already reserves a 5%
            # margin against capital), but a bumped order must never be sent
            # for more than the portfolio actually has.
            if price * quantity > self.portfolio.capital:
                return ExecutionResult(
                    symbol=symbol, action="no_action", price=price, quantity=0.0,
                    error="insufficient capital after precision rounding",
                )

        current_spread_pct = None
        if self.exchange is not None and self.max_spread_pct is not None:
            try:
                ticker = self.exchange.fetch_ticker(symbol)
                bid, ask = ticker.get("bid"), ticker.get("ask")
            except Exception:  # noqa: BLE001 - a failed quote check must not block a trade on a data gap
                bid, ask = None, None
            current_spread_pct = spread_pct(bid, ask)
            if spread_too_wide(bid, ask, self.max_spread_pct):
                logger.warning(
                    "rejecting buy - bid-ask spread too wide right now",
                    extra={"context": {"symbol": symbol, "bid": bid, "ask": ask, "max_spread_pct": self.max_spread_pct}},
                )
                return ExecutionResult(
                    symbol=symbol, action="no_action", price=price, quantity=0.0, error="spread too wide"
                )

        # A marketable limit order (capped price) instead of an unbounded
        # market order when the spread is comfortably tight - see
        # trading_assistant.execution.order_type. Falls back to "market"
        # when disabled, or when there's no spread reading to base the
        # choice on (a data gap must default to the simpler, always-fills
        # path, not an untested new one).
        order_type = (
            choose_order_type(current_spread_pct, self.max_spread_pct) if self.enable_adaptive_order_type else "market"
        )

        try:
            order = None
            latency_ms = None
            if self.exchange is not None:
                start = time.perf_counter()
                if order_type == "limit":
                    limit_price = marketable_limit_price(price, "buy")
                    order = self._call_with_retry(
                        self.exchange.create_limit_buy_order, symbol, quantity, limit_price
                    )
                else:
                    order = self._call_with_retry(self.exchange.create_market_buy_order, symbol, quantity)
                latency_ms = (time.perf_counter() - start) * 1000
            # A market order is not guaranteed to fill completely or at
            # exactly the last-seen price - preferring the exchange's own
            # reported fill quantity/price over what was requested matters
            # most right here, since this is the cost basis everything
            # downstream (capital, PnL, a later sell's quantity) inherits.
            fill_quantity, fill_price = self._resolve_fill(order, quantity, price)
            slippage_pct = compute_slippage_pct(price, fill_price, side="buy")
            if fill_quantity <= 0:
                if order_type == "limit" and self.exchange is not None and isinstance(order, dict) and order.get("id"):
                    # A marketable limit is priced to fill immediately in
                    # normal conditions, but isn't a guarantee - an unfilled
                    # order left resting on the exchange would otherwise be
                    # a silently orphaned position risk, forgotten the
                    # moment this method returns "no_action".
                    try:
                        self.exchange.cancel_order(order.get("id"), symbol)
                    except Exception:  # noqa: BLE001 - likely already filled/gone between the check and this call
                        logger.info(
                            "could not cancel unfilled limit entry order (likely already filled or gone)",
                            extra={"context": {"symbol": symbol, "order_id": order.get("id")}},
                        )
                logger.error(
                    "buy order reported zero fill - not opening a position",
                    extra={"context": {"symbol": symbol, "order": order}},
                )
                return ExecutionResult(
                    symbol=symbol, action="no_action", price=price, quantity=0.0, error="zero fill reported"
                )
            opened = self.portfolio.open_position(symbol, fill_price, fill_quantity)
            if not opened:
                # Should be unreachable given the pre-check above unless capital
                # changed between the check and this call. If it happens, a real
                # exchange fill now has no matching portfolio record - that's a
                # desync needing operator attention, not a routine no-op.
                logger.error(
                    "exchange order filled but portfolio failed to record it - capital/position desync",
                    extra={"context": {"symbol": symbol, "price": fill_price, "quantity": fill_quantity}},
                )
                return ExecutionResult(
                    symbol=symbol,
                    action="error",
                    price=fill_price,
                    quantity=fill_quantity,
                    error="portfolio desync: exchange order filled but position not recorded",
                )
            # A caller-supplied stop_loss_pct (e.g. RiskAgent's volatility-
            # adjusted per-trade distance) takes precedence over this
            # agent's own static default - the protective stop actually
            # placed on the exchange must match whatever distance position
            # sizing already assumed, or the two would silently disagree.
            effective_stop_pct = stop_loss_pct if stop_loss_pct is not None else self.stop_loss_pct
            stop_order_placed: Optional[bool] = None
            if self.exchange is not None and effective_stop_pct is not None:
                order_id = place_protective_stop(self.exchange, symbol, fill_quantity, fill_price, effective_stop_pct)
                self.portfolio.positions[symbol]["stop_order_id"] = order_id
                # Tracked so the trailing-stop updater (Orchestrator) has a
                # baseline to compare a candidate higher stop against,
                # without needing to re-derive it from entry_price every
                # cycle (which would break once the stop has already moved).
                self.portfolio.positions[symbol]["stop_price"] = (
                    fill_price * (1 - effective_stop_pct) if order_id else None
                )
                # Kept even when placement FAILED (order_id is None) - a
                # real incident left a position with a failed initial stop
                # and no per-cycle retry (only a full restart re-attempted
                # it, via reconcile_with_exchange at warm start), so a
                # position could sit genuinely unprotected indefinitely
                # under 24/7 operation. Orchestrator._retry_missing_
                # protective_stop needs this exact distance - not a
                # generic default - to reproduce the same target every
                # cycle until it succeeds.
                self.portfolio.positions[symbol]["intended_stop_loss_pct"] = effective_stop_pct
                stop_order_placed = order_id is not None
            return ExecutionResult(
                symbol=symbol,
                action="opened_position",
                price=fill_price,
                quantity=fill_quantity,
                slippage_pct=slippage_pct,
                latency_ms=latency_ms,
                order_type=order_type,
                stop_order_placed=stop_order_placed,
            )
        except Exception as exc:  # noqa: BLE001 - exchange errors are reported, not fatal to the loop
            logger.error("buy order failed", extra={"context": {"symbol": symbol, "error": str(exc)}})
            return ExecutionResult(symbol=symbol, action="error", price=price, quantity=0.0, error=str(exc))

    def _min_notional(self, symbol: str) -> Optional[float]:
        if self.exchange is None:
            return None
        try:
            market = self.exchange.market(symbol)
            return market.get("limits", {}).get("cost", {}).get("min")
        except Exception:  # noqa: BLE001 - a failed lookup must not block a trade
            return None

    def _sell(self, symbol: str, price: float) -> ExecutionResult:
        quantity = self.portfolio.positions[symbol]["quantity"]
        stop_order_id = self.portfolio.positions[symbol].get("stop_order_id")

        # A held position can shrink (price drift, partial fills) below the
        # exchange's own minimum order value - a real incident hit this for
        # BTC/USDT (a ~$4.44 remainder against a $5.00 minimum): the sell
        # was attempted anyway, Binance's NOTIONAL filter rejected it 3
        # times in a row, and the circuit breaker tripped on a sizing
        # mismatch this system could have known about in advance, not a
        # genuine execution failure. Checked BEFORE cancelling the resting
        # protective stop below - if we're not going to sell, the position
        # must keep whatever protection it already has, not lose it for
        # nothing. Mirrors _buy's identical defensive check above.
        min_notional = self._min_notional(symbol)
        if min_notional is not None and price * quantity < min_notional:
            logger.warning(
                "skipping sell - held position value is below the exchange's real minimum for this "
                "symbol, a sell order would just be rejected",
                extra={
                    "context": {"symbol": symbol, "order_value": price * quantity, "exchange_min_notional": min_notional}
                },
            )
            return ExecutionResult(
                symbol=symbol, action="no_action", price=price, quantity=0.0,
                error=f"position value ${price * quantity:.2f} below exchange minimum ${min_notional:.2f} - cannot be sold at this size",
            )

        try:
            order = None
            latency_ms = None
            if self.exchange is not None:
                # A resting protective stop holds `quantity` in reserve (not
                # free balance) - it must be cancelled before a market sell
                # for the same quantity, or the market sell would fail with
                # insufficient free balance.
                cancel_protective_stop(self.exchange, symbol, stop_order_id)
                start = time.perf_counter()
                order = self._call_with_retry(self.exchange.create_market_sell_order, symbol, quantity)
                latency_ms = (time.perf_counter() - start) * 1000
            fill_quantity, fill_price = self._resolve_fill(order, quantity, price)
            # quantity=fill_quantity (not None): a market sell that only
            # PARTIALLY fills must not be recorded as a full close, or the
            # portfolio would believe it's flat while the exchange still
            # holds the unsold remainder - close_position's partial-quantity
            # support (see PortfolioManager) handles this correctly either way.
            pnl = self.portfolio.close_position(symbol, fill_price, quantity=fill_quantity)
            stop_order_placed = self._reprotect_remainder(symbol)
            return ExecutionResult(
                symbol=symbol,
                action="closed_position",
                price=fill_price,
                quantity=fill_quantity,
                pnl=pnl,
                slippage_pct=compute_slippage_pct(price, fill_price, side="sell"),
                latency_ms=latency_ms,
                order_type="market",
                stop_order_placed=stop_order_placed,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("sell order failed", extra={"context": {"symbol": symbol, "error": str(exc)}})
            return ExecutionResult(symbol=symbol, action="error", price=price, quantity=quantity, error=str(exc))

    def execute_partial_sell(self, symbol: str, sell_quantity: float, price: float) -> ExecutionResult:
        """Sell PART of an open position (partial profit-taking) and
        re-protect whatever remains with a fresh resting stop, rather than
        the all-or-nothing close `_sell` performs. The existing resting
        stop must be cancelled first (it reserves the FULL original
        quantity, which would make even a partial market sell fail with
        insufficient free balance) and a new one placed for the reduced
        quantity afterward - a position coming out of this method must
        never be left holding shares with no protective floor at all."""
        if self.execution_mode != "autonomous":
            return ExecutionResult(
                symbol=symbol,
                action="no_action",
                price=price,
                quantity=0.0,
                error="autonomous execution disabled: manual_assist",
            )

        position = self.portfolio.positions.get(symbol)
        if position is None or sell_quantity <= 0:
            return ExecutionResult(symbol=symbol, action="no_action", price=price, quantity=0.0)

        sell_quantity = min(sell_quantity, position["quantity"])
        stop_order_id = position.get("stop_order_id")

        min_notional = self._min_notional(symbol)
        if min_notional is not None and price * sell_quantity < min_notional:
            logger.warning(
                "skipping partial sell - the requested slice's value is below the exchange's real "
                "minimum for this symbol, a sell order would just be rejected",
                extra={
                    "context": {
                        "symbol": symbol, "order_value": price * sell_quantity, "exchange_min_notional": min_notional,
                    }
                },
            )
            return ExecutionResult(
                symbol=symbol, action="no_action", price=price, quantity=0.0,
                error=f"position value ${price * sell_quantity:.2f} below exchange minimum ${min_notional:.2f} - cannot be sold at this size",
            )

        try:
            order = None
            latency_ms = None
            if self.exchange is not None:
                cancel_protective_stop(self.exchange, symbol, stop_order_id)
                start = time.perf_counter()
                order = self._call_with_retry(self.exchange.create_market_sell_order, symbol, sell_quantity)
                latency_ms = (time.perf_counter() - start) * 1000

            fill_quantity, fill_price = self._resolve_fill(order, sell_quantity, price)
            pnl = self.portfolio.close_position(symbol, fill_price, quantity=fill_quantity)
            stop_order_placed = self._reprotect_remainder(symbol)

            return ExecutionResult(
                symbol=symbol,
                action="partial_close",
                price=fill_price,
                quantity=fill_quantity,
                pnl=pnl,
                slippage_pct=compute_slippage_pct(price, fill_price, side="sell"),
                latency_ms=latency_ms,
                order_type="market",
                stop_order_placed=stop_order_placed,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("partial sell failed", extra={"context": {"symbol": symbol, "error": str(exc)}})
            return ExecutionResult(symbol=symbol, action="error", price=price, quantity=0.0, error=str(exc))

    def _reprotect_remainder(self, symbol: str) -> Optional[bool]:
        """After any sell that turned out to be partial - a deliberate
        partial profit-take, or a market SELL that itself only partially
        filled - whatever quantity is still open must get a fresh resting
        stop. The old one was already cancelled before the sell was
        attempted, so a partial fill would otherwise leave the remainder
        completely unprotected until the next cycle (or forever, if
        nothing else notices).

        Returns None when there's nothing to re-protect (position fully
        closed, no exchange, or no stop level to use), True on success,
        False if placement was attempted and failed - the caller uses this
        to decide whether the resulting ExecutionResult needs to flag a
        genuinely unprotected position."""
        remaining = self.portfolio.positions.get(symbol)
        if remaining is None or self.exchange is None:
            return None
        # Reuse whatever stop level the position already had (possibly
        # already raised by the trailing-stop updater), falling back to a
        # fresh entry-based stop only if it never had one recorded.
        stop_price = remaining.get("stop_price") or (
            remaining["price"] * (1 - self.stop_loss_pct) if self.stop_loss_pct else None
        )
        if stop_price is None:
            return None
        new_order_id = place_stop_at_price(self.exchange, symbol, remaining["quantity"], stop_price)
        remaining["stop_order_id"] = new_order_id
        remaining["stop_price"] = stop_price if new_order_id is not None else None
        if new_order_id is None:
            logger.error(
                "failed to re-protect remaining position after a partial sell - UNPROTECTED",
                extra={"context": {"symbol": symbol}},
            )
            return False
        return True

    @staticmethod
    def _resolve_fill(order: Any, requested_quantity: float, requested_price: float) -> tuple[float, float]:
        """Prefer the exchange's own reported fill quantity/price over what
        was requested - a market order is not guaranteed to fill completely
        or at exactly the last-seen price. Falls back to the requested
        values when the order has no fill data to report (order is None -
        no real exchange configured - or a response, including this
        codebase's own lightweight test doubles, that doesn't populate
        these fields), which is exactly the pre-existing behavior every
        caller had before this resolution step existed."""
        if not isinstance(order, dict):
            return requested_quantity, requested_price
        filled = order.get("filled")
        average = order.get("average") or order.get("price")
        resolved_quantity = float(filled) if filled is not None else requested_quantity
        resolved_price = float(average) if average else requested_price
        return resolved_quantity, resolved_price

    def _call_with_retry(self, func: Callable, *args: Any, **kwargs: Any) -> Any:
        last_error: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                return func(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - retry on any exchange-raised error
                last_error = exc
                logger.warning(
                    "exchange call failed, retrying",
                    extra={"context": {"attempt": attempt, "max_retries": self.max_retries, "error": str(exc)}},
                )
                if attempt < self.max_retries:
                    time.sleep(self.backoff_seconds * attempt)
        raise last_error
