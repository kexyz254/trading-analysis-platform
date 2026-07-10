"""Plain data-access helpers for the web dashboard's API layer.

No Streamlit dependency here - these are used by webapp/server.py (FastAPI)
and could equally be reused by any other frontend. Every function reads
either the journal SQLite database or the live exchange - nothing here
fabricates data.
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from trading_assistant.config import Config, ReadinessConfig
from trading_assistant.health import compute_health_score
from trading_assistant.persistence.journal import Journal
from trading_assistant.portfolio.manager import (
    max_drawdown_from_equity_curve,
    performance_summary_from_trade_pnls,
    sharpe_ratio_from_equity_curve,
)
from trading_assistant.readiness import build_readiness_report
from trading_assistant.validation.backtest_correlation import (
    BacktestVsLiveRecord,
    SurvivorshipBiasRecord,
    build_records,
    build_survivorship_records,
)
from run_system import build_exchange

# Real Binance kline intervals only - no invented granularities.
TIMEFRAMES = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"]

# PortfolioAgent.reconcile_with_exchange used to count unexplained/faucet-
# seeded exchange balances (e.g. a single testnet WBTC grant worth ~$61k)
# at their full current market value as live strategy equity - fixed in
# PortfolioManager._tracked_quantity. Every equity_snapshot recorded before
# this instant (the first heartbeat_start of the first process to run the
# fix) reflects that incorrect accounting; blending it with corrected
# snapshots into one returns series produces a fabricated ~90% "max
# drawdown" and an equally meaningless Sharpe ratio from the one-time
# restatement itself, not from any real trading event. Real-portfolio
# Sharpe/drawdown/elapsed-time are computed only from snapshots at or after
# this cutoff - the pre-cutoff history stays in the journal untouched
# (never deleted, matching this system's own audit-trail principle), it's
# just excluded from statistics that assume a continuous, consistent
# equity series. Remove this once enough post-cutoff history has
# accumulated to make the cutoff moot on its own.
_EQUITY_ACCOUNTING_FIX_CUTOFF = "2026-07-09T22:35:26+00:00"


def _clean_real_equity_curve(journal: Journal, is_live: bool, limit: int = 5000) -> List[Dict[str, Any]]:
    """recent_equity_snapshots filtered to _EQUITY_ACCOUNTING_FIX_CUTOFF AND
    the CURRENT mode - the single source both real_portfolio_metrics and
    readiness_report use so they can never drift apart on which snapshots
    count as clean.

    is_live is a REQUIRED param, not optional-with-a-default: switching a
    long-running database from testnet to live (or back) must never let a
    caller silently forget to filter and blend the two histories into one
    "real portfolio" view - found live, via a real testnet trade at a
    wildly different capital scale showing up as today's live PnL the
    moment this system actually went live. Rows written before the is_live
    column existed are backfilled to 0/testnet (see Journal._migrate) -
    correct for this system's own history (testnet was the only mode that
    ever ran before its first live cycle), not a guess.
    """
    snapshots = journal.recent_equity_snapshots(limit=limit)
    target = 1 if is_live else 0
    return [
        s for s in snapshots
        if s["timestamp"] >= _EQUITY_ACCOUNTING_FIX_CUTOFF and s.get("is_live") == target
    ]


# A second, later, and separate bug in the same family:
# PortfolioManager.close_position used to credit the FULL sale proceeds of
# a reconciled/unexplained (e.g. faucet-seeded) position to capital and
# realized PnL, instead of excluding the unexplained share the way live
# valuation already did - fixed the same day. This is the timestamp of the
# first heartbeat_start of the first process to run the corrected
# close_position. Confirmed against logs/trading_system.log: every trade
# closed before this instant on a symbol that had ANY unexplained_quantity
# has a realized_pnl computed under the old, incorrect formula. Unlike the
# equity cutoff above, the 5 affected trade_audit rows were individually,
# manually verified and corrected via scripts/correct_trade_audit_
# unexplained_pnl.py (an append-only correction, per this system's own
# audit-trail principle - trade_audit's own trigger rejects UPDATE/DELETE
# outright). This cutoff instead protects the `trades` table (the
# operational log Sharpe/win-rate statistics read from, not the immutable
# audit trail) the same way _EQUITY_ACCOUNTING_FIX_CUTOFF protects
# equity_snapshots - simpler than replicating the same manual-verification
# correction for every trades row, and trades was never marketed as an
# immutable audit trail the way trade_audit specifically was.
_TRADE_PNL_ACCOUNTING_FIX_CUTOFF = "2026-07-10T03:51:19+00:00"


def _clean_trade_pnls(journal: Journal, is_live: bool, limit: int = 5000) -> List[float]:
    """recent_trades' pnl values filtered to _TRADE_PNL_ACCOUNTING_FIX_CUTOFF
    AND the current mode - see _clean_real_equity_curve's docstring for why
    is_live is required, not optional. The single source both
    real_portfolio_metrics and readiness_report use."""
    trades = journal.recent_trades(limit=limit)
    target = 1 if is_live else 0
    return [
        t["pnl"] for t in trades
        if t.get("pnl") is not None
        and t["timestamp"] >= _TRADE_PNL_ACCOUNTING_FIX_CUTOFF
        and t.get("is_live") == target
    ]


class TTLCache:
    """Minimal in-memory TTL cache - avoids hammering the exchange on every
    frontend poll without pulling in an extra dependency for something this small."""

    def __init__(self):
        self._store: Dict[Any, tuple[float, Any]] = {}

    def get_or_set(self, key: Any, ttl_seconds: float, factory: Callable[[], Any]) -> Any:
        now = time.monotonic()
        cached = self._store.get(key)
        if cached is not None and now - cached[0] < ttl_seconds:
            return cached[1]
        value = factory()
        self._store[key] = (now, value)
        return value


_cache = TTLCache()
_exchange = None


def get_exchange(config: Config):
    global _exchange
    if _exchange is None:
        _exchange = build_exchange(config)
    return _exchange


def market_overview(config: Config, quote_currency: str = "USDT") -> List[Dict[str, Any]]:
    def _fetch():
        exchange = get_exchange(config)
        tickers = exchange.fetch_tickers()
        rows = []
        for symbol, ticker in tickers.items():
            if not symbol.endswith(f"/{quote_currency}"):
                continue
            rows.append(
                {
                    "symbol": symbol,
                    "last": ticker.get("last"),
                    "change_24h_pct": ticker.get("percentage"),
                    "quote_volume_24h": ticker.get("quoteVolume"),
                    "high_24h": ticker.get("high"),
                    "low_24h": ticker.get("low"),
                }
            )
        rows.sort(key=lambda r: r["quote_volume_24h"] or 0.0, reverse=True)
        return rows

    return _cache.get_or_set(("market_overview", quote_currency), ttl_seconds=30, factory=_fetch)


def fetch_ohlcv(config: Config, symbol: str, timeframe: str, limit: int = 300) -> List[Dict[str, Any]]:
    def _fetch():
        exchange = get_exchange(config)
        raw = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        return [
            {"timestamp": ts, "open": o, "high": h, "low": low, "close": c, "volume": v}
            for ts, o, h, low, c, v in raw
        ]

    return _cache.get_or_set(("ohlcv", symbol, timeframe, limit), ttl_seconds=5, factory=_fetch)


def active_symbols(db_path: str, config_symbols: List[str]) -> List[str]:
    """The actual currently-tracked universe, derived from recent decisions -
    not the static config seed list, which symbol_discovery expands on at
    runtime inside the live process and never persists anywhere else."""

    def _fetch():
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute("SELECT DISTINCT symbol FROM decisions").fetchall()
        except sqlite3.OperationalError:
            rows = []
        finally:
            conn.close()
        discovered = sorted(r[0] for r in rows)
        return discovered or list(config_symbols)

    return _cache.get_or_set(("active_symbols", db_path), ttl_seconds=15, factory=_fetch)


def paper_leaderboard(db_path: str, symbols: List[str], paper_capital: float) -> List[Dict[str, Any]]:
    """Latest paper bankroll per symbol/candidate across the currently active
    universe - the cross-strategy comparison view. Every row here comes from
    a real (simulated) portfolio the system has actually been running, not a
    projection. pnl_pct is relative to each candidate's starting paper
    capital (config.tournament.paper_capital), not the real portfolio's."""

    def _fetch():
        journal = Journal(db_path=db_path)
        rows = []
        for symbol in symbols:
            latest = journal.latest_paper_equity_by_candidate(symbol)
            for candidate, snapshot in latest.items():
                pnl_pct = (snapshot["total_equity"] - paper_capital) / paper_capital * 100.0
                rows.append(
                    {
                        "symbol": symbol,
                        "candidate": candidate,
                        "capital": snapshot["capital"],
                        "total_equity": snapshot["total_equity"],
                        "pnl_pct": pnl_pct,
                        "num_trades": snapshot["num_trades"],
                        "timestamp": snapshot["timestamp"],
                    }
                )
        rows.sort(key=lambda r: r["pnl_pct"], reverse=True)
        return rows

    return _cache.get_or_set(("paper_leaderboard", db_path, tuple(symbols)), ttl_seconds=5, factory=_fetch)


def paper_equity_history(db_path: str, symbol: str, candidate: Optional[str] = None) -> List[Dict[str, Any]]:
    def _fetch():
        journal = Journal(db_path=db_path)
        return journal.recent_paper_equity_snapshots(symbol=symbol, candidate=candidate, limit=2000)

    return _cache.get_or_set(("paper_equity_history", db_path, symbol, candidate), ttl_seconds=5, factory=_fetch)


def recent_alerts(db_path: str, limit: int = 100) -> List[Dict[str, Any]]:
    def _fetch():
        journal = Journal(db_path=db_path)
        return journal.recent_alerts(limit=limit)

    return _cache.get_or_set(("recent_alerts", db_path, limit), ttl_seconds=5, factory=_fetch)


def execution_quality_summary(db_path: str, limit: int = 500) -> Dict[str, Any]:
    """Aggregated real slippage/latency stats per symbol, plus the raw
    recent records - "measure slippage by pair/order type" and "latency
    and API reliability are monitored" from the Execution Quality
    checklist, computed from data ExecutionAgent already resolves from
    the exchange's own order responses."""

    def _fetch():
        journal = Journal(db_path=db_path)
        records = journal.recent_execution_quality(limit=limit)
        by_symbol: Dict[str, List[Dict[str, Any]]] = {}
        for r in records:
            by_symbol.setdefault(r["symbol"], []).append(r)

        summary_rows = []
        for symbol, rows in by_symbol.items():
            slippages = [r["slippage_pct"] for r in rows if r.get("slippage_pct") is not None]
            latencies = [r["latency_ms"] for r in rows if r.get("latency_ms") is not None]
            summary_rows.append(
                {
                    "symbol": symbol,
                    "num_orders": len(rows),
                    "avg_slippage_pct": (sum(slippages) / len(slippages)) if slippages else None,
                    "worst_slippage_pct": max(slippages) if slippages else None,
                    "avg_latency_ms": (sum(latencies) / len(latencies)) if latencies else None,
                }
            )
        summary_rows.sort(key=lambda r: r["num_orders"], reverse=True)
        return {"by_symbol": summary_rows, "recent": records[-100:]}

    return _cache.get_or_set(("execution_quality_summary", db_path, limit), ttl_seconds=15, factory=_fetch)


def trade_audit_records(db_path: str, limit: int = 200) -> List[Dict[str, Any]]:
    """The immutable trade audit trail, most-recent-first for display -
    every field a future "exactly how did this system perform" question
    needs (entry/exit price, strategy, confidence, regime, reason, realized
    PnL), sourced from a table that is never updated or deleted in place.
    A non-null corrects_id marks a correction row; the record it corrects
    is still present immediately below it in this same feed, never hidden."""

    def _fetch():
        journal = Journal(db_path=db_path)
        records = journal.recent_trade_audit(limit=limit)
        records.reverse()  # most-recent-first for the dashboard table
        return records

    return _cache.get_or_set(("trade_audit_records", db_path, limit), ttl_seconds=15, factory=_fetch)


def journal_snapshot(db_path: str, is_live: bool) -> Dict[str, Any]:
    def _fetch():
        journal = Journal(db_path=db_path)
        # Paper trading logs 5 candidate decisions per symbol per cycle
        # (ChampionChallengerAgent) into the SAME decisions table as the one
        # real decision - at 22 symbols that's ~132 rows/cycle, enough to
        # push real decisions out of a plain limit=200 window entirely.
        # Fetch a much larger raw batch, filter to real-only (paper trading
        # already has its own dedicated leaderboard/equity endpoints), then
        # cap at 200 for display.
        raw_decisions = journal.recent_decisions(limit=1000)
        real_decisions = [d for d in raw_decisions if not (d.get("details") or {}).get("paper")][:200]
        # Same is_live filter as _clean_real_equity_curve/_clean_trade_pnls
        # (see their docstrings) - a real testnet trade must never appear
        # in what's supposed to be "your real live trading history" once
        # this system has switched to live mode, or vice versa.
        target = 1 if is_live else 0
        raw_trades = journal.recent_trades(limit=200)
        mode_trades = [t for t in raw_trades if t.get("is_live") == target]
        return {
            # Filtered to _EQUITY_ACCOUNTING_FIX_CUTOFF - this feeds the
            # dashboard's live equity chart and "PnL today" stat directly,
            # both of which would otherwise show a fabricated ~$90k crash
            # at the exact moment the accounting fix landed (see
            # _clean_real_equity_curve's docstring). Deliberately the SAME
            # default limit real_portfolio_metrics/readiness_report use
            # (no override here) - a smaller window would make "first
            # snapshot" silently become a ROLLING window here once history
            # exceeds it, while the other two panels still anchor to the
            # true first post-cutoff snapshot, so "Return since first
            # snapshot" and "Elapsed operating time" could quietly
            # disagree about what "the start" means.
            "equity": _clean_real_equity_curve(journal, is_live=is_live),
            "decisions": real_decisions,
            "trades": mode_trades,
            "circuit_breaker": journal.latest_circuit_breaker_event(),
        }

    return _cache.get_or_set(("journal_snapshot", db_path, is_live), ttl_seconds=5, factory=_fetch)


_EVENT_TABLES = frozenset({"retrain_events", "tournament_events"})


def _read_events(db_path: str, table: str, limit: int = 100) -> List[Dict[str, Any]]:
    # `table` is interpolated into the query below since sqlite3 has no
    # parameterized-identifier syntax (only value placeholders) - safe only
    # because it's checked against a fixed allowlist first. Every current
    # caller already passes a hardcoded literal, but that alone is "safe by
    # accident": a future caller passing a request-derived table name would
    # turn this into SQL injection with no visible warning otherwise.
    if table not in _EVENT_TABLES:
        raise ValueError(f"table must be one of {sorted(_EVENT_TABLES)}, got {table!r}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(f"SELECT * FROM {table} ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()
    return [dict(row) for row in rows]


def retrain_events(db_path: str, limit: int = 100) -> List[Dict[str, Any]]:
    def _fetch():
        events = _read_events(db_path, "retrain_events", limit)
        for record in events:
            record["promoted"] = bool(record["promoted"])
            record["metrics"] = json.loads(record["metrics"]) if record["metrics"] else {}
        return events

    return _cache.get_or_set(("retrain_events", db_path, limit), ttl_seconds=5, factory=_fetch)


def tournament_events(db_path: str, limit: int = 200) -> List[Dict[str, Any]]:
    def _fetch():
        events = _read_events(db_path, "tournament_events", limit)
        for record in events:
            record["winners"] = json.loads(record["winners"])
            record["report"] = json.loads(record["report"])
        return events

    return _cache.get_or_set(("tournament_events", db_path, limit), ttl_seconds=5, factory=_fetch)


def winner_histories(db_path: str) -> Dict[str, Dict[str, List[Optional[str]]]]:
    """Reconstructs the exact chronological winner-per-regime history a live
    StrategyManager would have in winner_history, purely from the persisted
    tournament_events table - lets stability_score be recomputed identically
    in this separate process without needing the live orchestrator."""

    def _fetch():
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("SELECT * FROM tournament_events ORDER BY id ASC").fetchall()
        except sqlite3.OperationalError:
            rows = []
        finally:
            conn.close()

        histories: Dict[str, Dict[str, List[Optional[str]]]] = {}
        for row in rows:
            symbol = row["symbol"]
            winners = json.loads(row["winners"])
            by_regime = histories.setdefault(symbol, {})
            for regime, selection in winners.items():
                by_regime.setdefault(regime, []).append(selection.get("winner") if selection else None)
        return histories

    return _cache.get_or_set(("winner_histories", db_path), ttl_seconds=15, factory=_fetch)


def real_portfolio_metrics(db_path: str, is_live: bool) -> Dict[str, Any]:
    """Sharpe/drawdown/win-rate for the REAL portfolio in the CURRENT mode
    (testnet or live - see _clean_real_equity_curve's docstring for why
    is_live is required, not optional), computed from persisted
    equity_snapshots/trades via the same pure-function math paper trading's
    ChallengerReport already uses - the real portfolio deserves the same
    quality of performance reporting, not less."""
    def _fetch():
        journal = Journal(db_path=db_path)
        equity_snapshots = _clean_real_equity_curve(journal, is_live=is_live)
        equity_curve = [s["total_equity"] for s in equity_snapshots]
        trade_pnls = _clean_trade_pnls(journal, is_live=is_live)

        return {
            "num_equity_snapshots": len(equity_curve),
            "sharpe_ratio": float(sharpe_ratio_from_equity_curve(equity_curve)) if equity_curve else 0.0,
            "max_drawdown": float(max_drawdown_from_equity_curve(equity_curve)) if equity_curve else 0.0,
            **(performance_summary_from_trade_pnls(trade_pnls) or {"total_trades": 0, "win_rate": 0}),
            "first_snapshot_timestamp": equity_snapshots[0]["timestamp"] if equity_snapshots else None,
        }

    return _cache.get_or_set(("real_portfolio_metrics", db_path, is_live), ttl_seconds=10, factory=_fetch)


def _parse_iso(timestamp: Optional[str]) -> Optional[datetime]:
    if not timestamp:
        return None
    normalized = timestamp if ("Z" in timestamp or "+" in timestamp) else timestamp + "+00:00"
    normalized = normalized.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def system_health(db_path: str, poll_interval_seconds: float) -> Dict[str, Any]:
    """The engine (run_system.py) and this dashboard are separate processes
    that only communicate through the journal - this reads the heartbeat
    rows the engine writes (see Orchestrator.run_forever) and computes a
    real, explainable health score via trading_assistant.health, never a
    fabricated number. An engine process that predates this feature (or a
    fresh database) simply has no heartbeat rows yet - reported honestly
    as unknown/unavailable, not defaulted to "healthy"."""

    def _fetch():
        journal = Journal(db_path=db_path)
        start = journal.latest_heartbeat_start()
        latest_cycle = journal.latest_heartbeat_cycle()
        recent_cycles = journal.recent_heartbeat_cycles(limit=20)
        cb_event = journal.latest_circuit_breaker_event()

        now = datetime.now(timezone.utc)
        started_at = _parse_iso(start["timestamp"]) if start else None
        last_cycle_at = _parse_iso(latest_cycle["timestamp"]) if latest_cycle else None

        uptime_seconds = (now - started_at).total_seconds() if started_at else None
        # Staleness is measured from whichever is more recent of the last
        # completed cycle or the process start - right after a fresh start,
        # before the first cycle has finished, "staleness" should reflect
        # time-since-start, not be undefined/zero. Must be an actual
        # recency comparison (max), not `or`: `or` picks last_cycle_at
        # whenever it exists at all, even if it's a stale row left over
        # from BEFORE a crash/restart - record_heartbeat_start() writes a
        # fresh row immediately on restart, but the latest *cycle* row
        # only updates once the first post-restart cycle finishes (which
        # can take the full warm-start duration, 15-25 minutes for a large
        # symbol universe) - `or` would report staleness from the old,
        # pre-crash cycle for that whole window, falsely showing
        # degraded/critical health while the system is actually healthy
        # and busy warming up.
        candidates = [ts for ts in (last_cycle_at, started_at) if ts is not None]
        reference = max(candidates) if candidates else None
        staleness_seconds = (now - reference).total_seconds() if reference else None

        error_cycles = [c for c in recent_cycles if c.get("status") == "error"]
        recent_error_rate = (len(error_cycles) / len(recent_cycles)) if recent_cycles else None

        report = compute_health_score(
            has_heartbeat=start is not None,
            circuit_breaker_tripped=bool(cb_event and cb_event.get("tripped")),
            staleness_seconds=staleness_seconds,
            poll_interval_seconds=poll_interval_seconds,
            recent_error_rate=recent_error_rate,
        )

        return {
            **report.to_dict(),
            "uptime_seconds": uptime_seconds,
            "process_started_at": start["timestamp"] if start else None,
            "last_heartbeat_at": latest_cycle["timestamp"] if latest_cycle else None,
            "last_heartbeat_status": latest_cycle["status"] if latest_cycle else None,
            "staleness_seconds": staleness_seconds,
            "recent_error_rate": recent_error_rate,
            "recent_cycles_sampled": len(recent_cycles),
            "circuit_breaker_tripped": bool(cb_event and cb_event.get("tripped")),
        }

    return _cache.get_or_set(("system_health", db_path), ttl_seconds=5, factory=_fetch)


def drift_status(db_path: str, symbols: List[str]) -> List[Dict[str, Any]]:
    """Latest feature-drift verdict per symbol - a dashboard view of
    trading_assistant.ml.drift's PSI checks, which run every live cycle
    (see LearningAgent.check_feature_drift). A symbol with no entry here
    simply has no baseline yet (no model has been promoted for it) - never
    fabricated as "no drift"."""

    def _fetch():
        journal = Journal(db_path=db_path)
        rows = []
        for symbol in symbols:
            events = journal.recent_drift_events(symbol=symbol, limit=1)
            if events:
                rows.append(events[0])
        return rows

    return _cache.get_or_set(("drift_status", db_path, tuple(symbols)), ttl_seconds=15, factory=_fetch)


def _fetch_tournament_and_live_performance(
    journal: Journal, symbols: List[str]
) -> tuple:
    """Shared by backtest_live_correlation_records and
    survivorship_bias_records - both need the exact same two things (each
    symbol's latest tournament report/winners, and each symbol/candidate's
    live paper Sharpe), so fetching once avoids duplicate journal queries
    and guarantees the two views can never see different data."""
    tournament_reports: Dict[str, Any] = {}
    winners: Dict[str, Dict[str, Optional[str]]] = {}
    for symbol in symbols:
        event = journal.latest_tournament_event(symbol)
        if event:
            tournament_reports[symbol] = event["report"]
            winners[symbol] = {regime: sel.get("winner") for regime, sel in event["winners"].items()}

    live_performance: Dict[str, Dict[str, Any]] = {}
    for symbol in symbols:
        latest = journal.latest_paper_equity_by_candidate(symbol)
        for candidate in latest:
            snapshots = journal.recent_paper_equity_snapshots(symbol=symbol, candidate=candidate, limit=2000)
            curve = [s["total_equity"] for s in snapshots]
            num_trades = snapshots[-1]["num_trades"] if snapshots else 0
            sharpe = float(sharpe_ratio_from_equity_curve(curve)) if curve else 0.0
            live_performance.setdefault(symbol, {})[candidate] = {
                "sharpe_ratio": sharpe,
                "num_trades": num_trades,
            }

    return tournament_reports, winners, live_performance


def backtest_live_correlation_records(
    db_path: str, symbols: List[str], min_live_trades: int = 5
) -> List[Dict[str, Any]]:
    """Pairs each symbol/candidate's latest walk-forward backtest Sharpe
    (from tournament_events) with its live paper Sharpe (computed from
    paper_equity_snapshots via the same Sharpe math ChallengerReport
    uses) - the raw evidence behind the "Backtest-vs-live correlation"
    readiness criterion. See trading_assistant.validation.backtest_correlation."""

    def _fetch():
        journal = Journal(db_path=db_path)
        tournament_reports, _winners, live_performance = _fetch_tournament_and_live_performance(journal, symbols)
        records = build_records(tournament_reports, live_performance, min_live_trades=min_live_trades)
        return [r.to_dict() for r in records]

    return _cache.get_or_set(
        ("backtest_live_correlation_records", db_path, tuple(symbols), min_live_trades), ttl_seconds=30, factory=_fetch
    )


def survivorship_bias_records(
    db_path: str, symbols: List[str], min_live_trades: int = 5
) -> List[Dict[str, Any]]:
    """Each (symbol, regime)'s live champion, compared against the
    unbiased average walk-forward Sharpe of EVERY candidate the tournament
    tested for that symbol - not just the champion's own (survivorship-
    biased) backtest number. Non-blocking diagnostic, surfaced as
    ReadinessReport.warnings - see
    trading_assistant.validation.backtest_correlation.average_backtest_sharpe
    for why the champion's own backtest Sharpe is an overly flattering
    baseline to hold live performance to."""

    def _fetch():
        journal = Journal(db_path=db_path)
        tournament_reports, winners, live_performance = _fetch_tournament_and_live_performance(journal, symbols)
        records = build_survivorship_records(tournament_reports, winners, live_performance, min_live_trades=min_live_trades)
        return [r.to_dict() for r in records]

    return _cache.get_or_set(
        ("survivorship_bias_records", db_path, tuple(symbols), min_live_trades), ttl_seconds=30, factory=_fetch
    )


def readiness_report(
    db_path: str, config: ReadinessConfig, is_live: bool, symbols: Optional[List[str]] = None
) -> Dict[str, Any]:
    def _fetch():
        journal = Journal(db_path=db_path)
        histories = winner_histories(db_path)
        metrics = real_portfolio_metrics(db_path, is_live=is_live)
        equity_snapshots = _clean_real_equity_curve(journal, is_live=is_live)
        equity_curve = [s["total_equity"] for s in equity_snapshots]
        trade_pnls = _clean_trade_pnls(journal, is_live=is_live)
        cb_event = journal.latest_circuit_breaker_event()

        backtest_live_records = None
        survivorship_records = None
        if symbols:
            raw_records = backtest_live_correlation_records(
                db_path, symbols, min_live_trades=config.min_live_trades_for_correlation
            )
            backtest_live_records = [BacktestVsLiveRecord(**r) for r in raw_records]
            raw_survivorship = survivorship_bias_records(
                db_path, symbols, min_live_trades=config.min_live_trades_for_correlation
            )
            survivorship_records = [SurvivorshipBiasRecord(**r) for r in raw_survivorship]

        report = build_readiness_report(
            winner_histories=histories,
            real_equity_curve=equity_curve,
            real_trade_pnls=trade_pnls,
            first_timestamp=metrics.get("first_snapshot_timestamp"),
            circuit_breaker_event=cb_event,
            config=config,
            backtest_live_records=backtest_live_records,
            survivorship_records=survivorship_records,
        )
        return report.to_dict()

    return _cache.get_or_set(
        ("readiness_report", db_path, tuple(symbols or []), is_live), ttl_seconds=15, factory=_fetch
    )
