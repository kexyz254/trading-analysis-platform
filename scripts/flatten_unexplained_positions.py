"""Sell every position that's FULLY unexplained by this system's own trade
history - e.g. testnet faucet-seeded balances the strategy never actually
bought (see PortfolioAgent.reconcile_with_exchange). Purely a bookkeeping
cleanup ("stop showing exchange dust I never traded" on the dashboard's
Open Positions panel) - unlike scripts/emergency_flatten.py, this does
NOT trip the circuit breaker and NEVER touches a position the strategy
genuinely opened (including a partially-explained/mixed one).

Usage:
    python scripts/flatten_unexplained_positions.py --reason "dashboard cleanup"
    python scripts/flatten_unexplained_positions.py --reason "..." --config my.yaml
    python scripts/flatten_unexplained_positions.py --reason "..." --yes   # skip the interactive prompt

Connects to the SAME exchange/journal the live run_system.py process uses
- does not need the live process to be running, and works whether or not
it is: if run_system.py is currently up, its own next warm-start
reconciliation will observe the exchange-side closures and its in-memory
state will resync naturally, same as it already does after any restart.
"""

from __future__ import annotations

import argparse
import logging
import sys

from trading_assistant.config import load_config
from trading_assistant.logging_setup import configure_logging
from run_system import build_orchestrator

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sell every fully-unexplained (e.g. faucet-seeded) position")
    parser.add_argument("--reason", required=True, help="Why this is being invoked - recorded in the journal")
    parser.add_argument("--config", default=None, help="Path to config.yaml")
    parser.add_argument("--yes", action="store_true", help="Skip the interactive confirmation prompt")
    args = parser.parse_args()

    config = load_config(args.config)
    configure_logging(level=config.logging.level, log_file=config.logging.file)

    print(f"Connecting to {config.exchange.name} in {'LIVE' if config.is_live else 'TESTNET'} mode...")
    orchestrator = build_orchestrator(config)

    # build_orchestrator() alone returns a FRESH, empty PortfolioManager with
    # market_monitor.symbols still at config.yaml's small startup seed (e.g.
    # just BTC/USDT, ETH/USDT) - the real discovered universe (~20-30 pairs)
    # normally only gets populated by run_forever()'s warm-start (symbol
    # discovery, THEN reconcile_with_exchange against that full universe),
    # which this standalone script never runs. reconcile_with_exchange is
    # deliberately SCOPED to whatever symbol list it's given, so without
    # replicating that same warm-start step here, this script would silently
    # miss reconciling (and therefore selling) any symbol outside the seed -
    # including exactly the kind of dust balance it exists to clean up
    # (confirmed live: a first version of this script that only widened via
    # the journal's last known snapshot missed EUR/USDT, a phantom balance
    # that had never yet made it into a snapshot because it fell outside a
    # narrower, previously-tracked universe).
    if orchestrator.symbol_discovery is not None:
        orchestrator._refresh_symbol_universe()  # noqa: SLF001 - same warm-start step run_forever() runs
    last_snapshot = orchestrator.journal.latest_equity_snapshot() if orchestrator.journal is not None else None
    known_symbols = set(last_snapshot["positions"].keys()) if last_snapshot else set()
    reconcile_symbols = sorted(set(orchestrator.market_monitor.symbols) | known_symbols)

    # market_monitor.poll() (used below by flatten_unexplained_positions to
    # get a fresh sell price) only fetches for market_monitor.symbols, which
    # is a SEPARATE attribute from what's passed to reconcile_with_exchange
    # above - widening one without the other leaves poll() unable to price
    # any symbol outside the original seed, so every sell attempt fails with
    # "could not get a fresh price" and nothing actually gets flattened.
    orchestrator.market_monitor.update_symbols(reconcile_symbols)

    print("Reconciling with real exchange balance...")
    orchestrator.portfolio.reconcile_with_exchange(
        orchestrator.execution.exchange, reconcile_symbols, orchestrator.execution.stop_loss_pct
    )

    unexplained_symbols = [
        symbol for symbol, position in orchestrator.portfolio.portfolio.positions.items()
        if position.get("unexplained_quantity", 0.0) >= position["quantity"]
    ]
    if not unexplained_symbols:
        print("No fully-unexplained positions found - nothing to do.")
        return

    print(f"About to sell {len(unexplained_symbols)} fully-unexplained position(s): {', '.join(unexplained_symbols)}")
    print("(Real strategy-opened positions, including partially-explained ones, are never touched by this script.)")

    if not args.yes:
        confirmation = input("Type FLATTEN to confirm: ")
        if confirmation.strip() != "FLATTEN":
            print("Aborted - no changes made.")
            sys.exit(1)

    results = orchestrator.flatten_unexplained_positions(args.reason)

    for symbol, result in results.items():
        print(f"  {symbol}: {result.action} (price={result.price}, qty={result.quantity}, pnl={result.pnl})")
    print("Done. Circuit breaker untouched - normal trading continues.")
    print("If run_system.py is currently running, restart it to refresh the dashboard's Open Positions view.")


if __name__ == "__main__":
    main()
