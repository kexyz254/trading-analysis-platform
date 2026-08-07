"""One-time, append-only correction for a specific, diagnosed accounting
bug in PortfolioManager.close_position (fixed 2026-07-10): selling a
position reconciled from an exchange balance the journal's trade history
couldn't explain (e.g. a testnet faucet grant) used to attribute its full
realized PnL to the strategy, instead of excluding the unexplained share
the same way live valuation already did. See close_position's current
docstring/comments for the fix itself.

This script does NOT delete or edit anything - trade_audit's own triggers
reject UPDATE/DELETE outright, and this system's stated design principle
(never mutate an audit record, always append a correction referencing it)
is followed exactly via Journal.correct_trade_audit.

Every corrected pnl value below was derived from this exact evidence, not
guessed:
  - The affected symbol's unexplained_quantity was cross-checked against
    every "portfolio reconciliation" log line for that symbol in the
    hours surrounding the trade's exit_timestamp (logs/trading_system.log)
    - in every one of these 5 cases, journal_explained_qty was IDENTICAL
      across every reconciliation from well before the trade until the
      trade itself, so there is no ambiguity about which fraction applies.
  - corrected_pnl = (1 - unexplained_qty/quantity) * original_pnl. This
    is the exact identity close_position's fix reduces to: both the
    proceeds and the cost basis it nets against are scaled by the same
    tracked_fraction, so the corrected value is just that fraction of
    the original recorded figure - see close_position's implementation.

Run once: python scripts/correct_trade_audit_unexplained_pnl.py [--config my.yaml]
Safe to re-run: skips any id that already has a correction pointing at it.
"""

from __future__ import annotations

import argparse
import logging

from trading_assistant.config import load_config
from trading_assistant.logging_setup import configure_logging
from trading_assistant.persistence.journal import Journal
from trading_assistant.persistence.paths import trading_db_path

logger = logging.getLogger(__name__)

# id -> (corrected_pnl, evidence). Every id here was manually verified
# against logs/trading_system.log's reconciliation history for that exact
# symbol before this script was written - see the module docstring.
_CORRECTIONS = {
    1: (0.0, "ARB/USDT was 100% unexplained (journal_explained_qty=0.0) at every reconciliation surrounding this trade"),
    2: (0.0, "ARB/USDT was 100% unexplained (journal_explained_qty=0.0) at every reconciliation surrounding this trade"),
    3: (0.0, "ZEC/USDT was 100% unexplained (journal_explained_qty=0.0) at every reconciliation surrounding this trade"),
    4: (-31.105630908180245, "XRP/USDT was 82.18% explained (1863.07/2267.0) at every reconciliation surrounding this trade"),
    5: (0.0, "BNB/USDT was 100% unexplained (journal_explained_qty=0.0) at every reconciliation surrounding this trade"),
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Correct trade_audit realized_pnl for the diagnosed unexplained-quantity bug")
    parser.add_argument("--config", default=None, help="Path to config.yaml")
    parser.add_argument("--dry-run", action="store_true", help="Print what would change without writing anything")
    args = parser.parse_args()

    config = load_config(args.config)
    configure_logging(level=config.logging.level, log_file=config.logging.file)
    journal = Journal(db_path=trading_db_path())

    already_corrected = {
        row["corrects_id"] for row in journal.recent_trade_audit(limit=1000) if row["corrects_id"] is not None
    }

    for original_id, (corrected_pnl, evidence) in _CORRECTIONS.items():
        if original_id in already_corrected:
            logger.info(f"id={original_id} already has a correction on file - skipping")
            continue
        original = journal.get_trade_audit(original_id)
        if original is None:
            logger.warning(f"id={original_id} not found - skipping (already corrected elsewhere, or DB was reset)")
            continue

        logger.info(
            f"correcting trade_audit id={original_id} ({original['symbol']}): "
            f"realized_pnl {original['realized_pnl']:.4f} -> {corrected_pnl:.4f} ({evidence})"
        )
        if not args.dry_run:
            new_id = journal.correct_trade_audit(
                original_id,
                realized_pnl=corrected_pnl,
                exit_reason=(
                    f"{original['exit_reason']} | CORRECTED {original_id}->this: excludes unexplained/faucet "
                    f"quantity's PnL per the close_position accounting fix (2026-07-10) - {evidence}"
                ),
            )
            logger.info(f"appended correction id={new_id}")

    if args.dry_run:
        logger.info("dry run - nothing was written")


if __name__ == "__main__":
    main()
