"""Manually clear a tripped circuit breaker.

There is deliberately no dashboard button or API endpoint for this - same
reasoning as emergency_flatten.py: the dashboard has no authentication, and
a network-reachable "resume trading" action would be a real attack surface.
This script requires the same access the supervisor process itself already
assumes.

CircuitBreaker.reset() has no automatic equivalent by design (see
agents/circuit_breaker.py) - a trip must be a deliberate, reviewed decision,
never cleared by a restart or any code path on its own.

IMPORTANT: this script builds its own standalone Orchestrator/RiskAgent/
CircuitBreaker (same pattern as emergency_flatten.py) and writes the reset
to the SAME journal (trading_system.db) run_system.py reads from - it does
NOT reach into an already-running run_system.py process's memory. If
run_system.py is currently running, its in-memory circuit breaker stays
tripped until that process is next restarted (which re-reads the fresh
state from the journal at startup). If you're resetting alongside a
routine deploy/restart anyway, run this first, then restart the engine.

Usage:
    python scripts/reset_circuit_breaker.py --operator "your name"
    python scripts/reset_circuit_breaker.py --operator "your name" --config my.yaml
"""

from __future__ import annotations

import argparse
import sys

from trading_assistant.config import load_config
from trading_assistant.logging_setup import configure_logging
from run_system import build_orchestrator


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--operator", required=True, help="Who is resetting this - recorded in the journal")
    parser.add_argument("--config", default=None, help="Path to config.yaml")
    args = parser.parse_args()

    config = load_config(args.config)
    configure_logging(level=config.logging.level, log_file=config.logging.file)

    orchestrator = build_orchestrator(config)
    breaker = orchestrator.risk.circuit_breaker

    print(f"Current state: tripped={breaker.tripped}, reason={breaker.trip_reason!r}, tripped_at={breaker.tripped_at}")
    if not breaker.tripped:
        print("Circuit breaker is not currently tripped - nothing to do.")
        return

    confirmation = input("Type RESET to confirm - this resumes new buy entries system-wide: ")
    if confirmation.strip() != "RESET":
        print("Aborted - no changes made.")
        sys.exit(1)

    breaker.reset(operator=args.operator)
    print("Circuit breaker reset and recorded in the journal.")
    if_running_note = (
        "If run_system.py is currently running, this reset will only take effect once that process "
        "is restarted - its in-memory state doesn't change just because the journal did."
    )
    print(if_running_note)


if __name__ == "__main__":
    main()
