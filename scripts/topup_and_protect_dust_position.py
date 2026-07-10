"""One-off ops script: top up a real position that's fallen below the
exchange's minimum notional (so it can never get a protective stop placed,
or be sold either) by buying just enough additional quantity to clear the
minimum with a safety margin, then placing a real protective stop for the
combined quantity.

Run with the live engine STOPPED. This talks to the real exchange directly
and does not update run_system.py's own in-memory portfolio state - doing
this while the engine is running risks a race with its own
_retry_missing_protective_stop logic (both could try to place a stop for
the same position). The next warm start's reconcile_with_exchange call
picks up the new real balance; find_open_protective_stop makes the stop
placement here idempotent against that reconciliation, so it won't
duplicate the stop this script places.

Dry-run by default - prints the exact plan (current balance, computed
top-up quantity/cost, target) without touching the exchange. Pass
--execute only once you've reviewed that output and are ready to place a
real market buy order.

Usage:
    python scripts/topup_and_protect_dust_position.py --symbol BTC/USDT --target-notional 7.0
    python scripts/topup_and_protect_dust_position.py --symbol BTC/USDT --target-notional 7.0 --execute
"""

from __future__ import annotations

import argparse
import sys

from run_system import build_exchange
from trading_assistant.agents.protective_stops import find_open_protective_stop, place_protective_stop
from trading_assistant.config import load_config


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--symbol", required=True, help="e.g. BTC/USDT")
    parser.add_argument(
        "--target-notional", type=float, required=True,
        help="Target USD value for the combined position after top-up - should comfortably clear the exchange's minimum notional.",
    )
    parser.add_argument(
        "--stop-loss-pct", type=float, default=None,
        help="Stop distance as a fraction, e.g. 0.01 for 1%% (default: config.yaml's risk.stop_loss_pct)",
    )
    parser.add_argument("--execute", action="store_true", help="Actually place the buy/stop orders (default: dry-run, prints the plan only)")
    args = parser.parse_args()

    config = load_config()
    exchange = build_exchange(config)
    exchange.load_markets()

    base_asset, _quote_asset = args.symbol.split("/")
    market = exchange.market(args.symbol)
    min_notional = market.get("limits", {}).get("cost", {}).get("min")

    balance = exchange.fetch_balance()
    current_qty = float((balance.get(base_asset) or {}).get("free", 0.0) or 0.0)

    ticker = exchange.fetch_ticker(args.symbol)
    price = float(ticker["last"])
    current_notional = current_qty * price

    print(f"Exchange minimum notional for {args.symbol}: ${min_notional}")
    print(f"Current free balance: {current_qty} {base_asset} (~${current_notional:.2f} at ${price:,.2f})")

    # Binance's NOTIONAL filter applies to the ORDER itself, not the
    # resulting combined position - a top-up order sized only to close the
    # gap to target_notional can itself land below the exchange minimum
    # and get rejected (confirmed: this happened on the first real attempt
    # here). The order must clear min_notional on its own, with the same
    # safety margin ExecutionAgent._buy uses for the same reason
    # (fee/lot-step rounding can shave a few percent off after the fact).
    _MIN_NOTIONAL_SAFETY_MARGIN = 1.15
    needed_qty = 0.0
    if current_notional >= args.target_notional:
        print(f"Already at or above target (${args.target_notional:.2f}) - no top-up needed.")
    else:
        needed_notional = args.target_notional - current_notional
        if min_notional is not None:
            needed_notional = max(needed_notional, min_notional * _MIN_NOTIONAL_SAFETY_MARGIN)
        needed_qty = float(exchange.amount_to_precision(args.symbol, needed_notional / price))
        top_up_notional = needed_qty * price
        print(f"Plan: buy {needed_qty} {base_asset} (~${top_up_notional:.2f}) - order itself must clear the ${min_notional} minimum")

    if not args.execute:
        print("\nDRY RUN - no orders placed. Re-run with --execute to actually buy and protect this position.")
        return

    if needed_qty > 0:
        order = exchange.create_market_buy_order(args.symbol, needed_qty)
        filled = float(order.get("filled") or needed_qty)
        avg_price = float(order.get("average") or order.get("price") or price)
        print(f"BUY FILLED: {filled} {base_asset} at avg price {avg_price}")

    # Re-fetch the REAL post-buy balance rather than trusting the order
    # response's own accounting - the same "prefer the exchange's own
    # numbers" discipline ExecutionAgent._resolve_fill already applies.
    balance = exchange.fetch_balance()
    combined_qty = float((balance.get(base_asset) or {}).get("free", 0.0) or 0.0)
    ticker = exchange.fetch_ticker(args.symbol)
    price = float(ticker["last"])
    print(f"Combined position: {combined_qty} {base_asset} (~${combined_qty * price:.2f})")

    existing_stop = find_open_protective_stop(exchange, args.symbol)
    if existing_stop is not None:
        print(f"A protective stop already exists (order_id={existing_stop}) - not placing a duplicate.")
        return

    stop_loss_pct = args.stop_loss_pct if args.stop_loss_pct is not None else config.risk.stop_loss_pct
    order_id = place_protective_stop(exchange, args.symbol, combined_qty, price, stop_loss_pct)
    if order_id is None:
        print("FAILED to place protective stop - see logged exception above. Position remains unprotected.")
        sys.exit(1)
    print(f"PROTECTIVE STOP PLACED: order_id={order_id}, stop_loss_pct={stop_loss_pct}, reference_price={price}")


if __name__ == "__main__":
    main()
