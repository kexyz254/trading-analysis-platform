"""Emergency kill switch: immediately market-sell every open real position
and trip the circuit breaker.

Deliberately a standalone script, not a dashboard button or API endpoint -
the dashboard has no authentication at all (it's meant for local, trusted
use), and adding a network-reachable "liquidate everything" action would be
a real new attack surface. This script requires the same access the
supervisor process itself already assumes: whoever can run a script on this
machine already has full control over the running system.

Usage:
    python scripts/emergency_flatten.py --reason "manual intervention: unexpected losses"
    python scripts/emergency_flatten.py --reason "..." --config my.yaml

This connects to the SAME exchange/journal the live run_system.py process
uses (same config.yaml, same trading_system.db) - it does not need the
live process to be running, and works whether or not it is: if
run_system.py is currently up, its own next warm-start reconciliation will
observe the exchange-side closures and its in-memory state will resync
naturally, same as it already does after any restart.
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
    parser = argparse.ArgumentParser(description="Emergency kill switch: flatten every open real position now")
    parser.add_argument("--reason", required=True, help="Why this is being invoked - recorded in the journal/alerts")
    parser.add_argument("--config", default=None, help="Path to config.yaml")
    args = parser.parse_args()

    config = load_config(args.config)
    configure_logging(level=config.logging.level, log_file=config.logging.file)

    print(f"Connecting to {config.exchange.name} in {'LIVE' if config.is_live else 'TESTNET'} mode...")
    orchestrator = build_orchestrator(config)

    # build_orchestrator() alone returns a FRESH, empty PortfolioManager -
    # reconciliation (rebuilding positions from the real exchange balance)
    # normally only happens inside Orchestrator.run_forever()'s warm-start,
    # which this standalone script never runs. Without this explicit call,
    # emergency_flatten_all would see zero positions and do nothing useful
    # even while the exchange holds real, open ones.
    print("Reconciling with real exchange balance...")
    orchestrator.portfolio.reconcile_with_exchange(
        orchestrator.execution.exchange, orchestrator.market_monitor.symbols, orchestrator.execution.stop_loss_pct
    )

    open_symbols = list(orchestrator.portfolio.portfolio.positions.keys())
    if open_symbols:
        print(f"About to close {len(open_symbols)} position(s): {', '.join(open_symbols)}")
    else:
        print("No open positions found (fresh portfolio state) - will still trip the circuit breaker.")

    confirmation = input("Type FLATTEN to confirm: ")
    if confirmation.strip() != "FLATTEN":
        print("Aborted - no changes made.")
        sys.exit(1)

    results = orchestrator.emergency_flatten_all(args.reason)

    for symbol, result in results.items():
        print(f"  {symbol}: {result.action} (price={result.price}, qty={result.quantity}, pnl={result.pnl})")
    print(f"Done. Circuit breaker tripped: {orchestrator.risk.circuit_breaker.tripped}")
    print("Trading halted until an operator explicitly calls CircuitBreaker.reset() (or resets it via the live process).")


if __name__ == "__main__":
    main()
