"""Back up and reset only the Binance price-call paper track record."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from webapp.price_call import RealTimePriceCallEngine


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default="dashboard_data/analytics.db")
    parser.add_argument("--confirm", required=True)
    args = parser.parse_args()

    if args.confirm != "RESET":
        parser.error("--confirm must be exactly RESET")

    database = Path(args.database).resolve()
    if not database.is_file():
        parser.error(f"database does not exist: {database}")

    # Apply the current non-destructive schema migration first. This makes the
    # reset safe even when the new dashboard server has not yet been started.
    RealTimePriceCallEngine(str(database))

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-UTC")
    backup = database.with_name(
        f"{database.stem}-before-fresh-price-call-{stamp}{database.suffix}"
    )

    source = sqlite3.connect(str(database))
    destination = sqlite3.connect(str(backup))
    try:
        source.backup(destination)
    finally:
        destination.close()

    source.row_factory = sqlite3.Row
    source.execute("PRAGMA foreign_keys = ON")
    tables = {
        row["name"]
        for row in source.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    required = {
        "price_calls",
        "paper_bankroll_positions",
        "paper_bankroll_settlements",
        "paper_bankroll_accounts",
    }
    missing = sorted(required - tables)
    if missing:
        source.close()
        parser.error(f"missing required tables: {', '.join(missing)}")

    with source:
        removed = {
            "settlements": source.execute(
                "SELECT COUNT(*) FROM paper_bankroll_settlements"
            ).fetchone()[0],
            "positions": source.execute(
                "SELECT COUNT(*) FROM paper_bankroll_positions"
            ).fetchone()[0],
            "calls": source.execute(
                "SELECT COUNT(*) FROM price_calls"
            ).fetchone()[0],
        }
        source.execute("DELETE FROM paper_bankroll_settlements")
        source.execute("DELETE FROM paper_bankroll_positions")
        source.execute("DELETE FROM price_calls")
        source.execute(
            """
            UPDATE paper_bankroll_accounts
            SET starting_balance_text = '50.00',
                settled_balance_text = '50.00',
                high_watermark_text = '50.00',
                max_drawdown_text = '0',
                updated_at_ms = 0
            """
        )
        if "sqlite_sequence" in tables:
            source.execute("DELETE FROM sqlite_sequence WHERE name = 'price_calls'")

    remaining = {
        "calls": source.execute("SELECT COUNT(*) FROM price_calls").fetchone()[0],
        "positions": source.execute(
            "SELECT COUNT(*) FROM paper_bankroll_positions"
        ).fetchone()[0],
        "settlements": source.execute(
            "SELECT COUNT(*) FROM paper_bankroll_settlements"
        ).fetchone()[0],
    }
    integrity = source.execute("PRAGMA integrity_check").fetchone()[0]
    source.close()

    print(f"backup: {backup}")
    print(f"removed: {removed}")
    print(f"remaining: {remaining}")
    print(f"integrity: {integrity}")
    return 0 if integrity == "ok" and not any(remaining.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
