import pytest

from webapp import data_access as da
from trading_assistant.persistence.journal import Journal


def test_journal_snapshot_excludes_paper_decisions(tmp_path):
    """Regression: ChampionChallengerAgent logs 5 paper-trading candidate
    decisions per symbol per cycle into the same decisions table as the one
    real decision - at scale (22 symbols) that's enough noise to push real
    decisions entirely out of a plain limit-based window. journal_snapshot's
    "decisions" must be real-only; paper trading has its own dedicated
    leaderboard/equity endpoints."""
    db_path = str(tmp_path / "journal.db")
    journal = Journal(db_path=db_path)

    journal.record_decision(symbol="BTC/USDT", signal="HOLD", risk_approved=True, details={})
    for candidate in ["ml_ensemble", "sma_crossover", "rsi_mean_reversion", "macd", "bollinger_bands"]:
        journal.record_decision(
            symbol="BTC/USDT", signal="HOLD", risk_approved=True, details={"paper": True, "candidate": candidate}
        )

    snapshot = da.journal_snapshot(db_path, False)

    assert len(snapshot["decisions"]) == 1
    assert not snapshot["decisions"][0]["details"].get("paper")


def test_journal_snapshot_real_decisions_survive_heavy_paper_volume(tmp_path):
    """With many symbols, paper noise (5x per symbol per cycle) could push
    real decisions out of a naive limit=200 fetch entirely - the raw fetch
    window must be large enough that real decisions across many symbols
    still come through after filtering."""
    db_path = str(tmp_path / "journal.db")
    journal = Journal(db_path=db_path)

    for i in range(30):
        journal.record_decision(symbol=f"SYM{i}/USDT", signal="HOLD", risk_approved=True, details={})
        for candidate in ["ml_ensemble", "sma_crossover", "rsi_mean_reversion", "macd", "bollinger_bands"]:
            journal.record_decision(
                symbol=f"SYM{i}/USDT", signal="HOLD", risk_approved=True,
                details={"paper": True, "candidate": candidate},
            )

    snapshot = da.journal_snapshot(db_path, False)

    assert len(snapshot["decisions"]) == 30
    assert all(not d["details"].get("paper") for d in snapshot["decisions"])


def test_journal_snapshot_real_decisions_survive_paper_volume_exceeding_the_old_fixed_batch_size(tmp_path):
    """Regression for a real incident: with an 8+ candidate tournament
    pool across ~30 symbols running for hours, paper volume reached
    ~110x real volume (991 paper vs 9 real in the most recent 1000 rows)
    - a fetch-a-fixed-batch-then-filter-in-Python approach silently
    returned almost no real decisions for actively-trading symbols, no
    matter how large that fixed batch was, since paper volume alone can
    always grow to exceed it. Real decisions must survive regardless of
    how much paper volume was written AFTER them - proven here by writing
    real decisions FIRST, then more paper noise than the old fixed
    fetch-size (1000) alone, which would have pushed every real row out
    of that window entirely under the old code."""
    db_path = str(tmp_path / "journal.db")
    journal = Journal(db_path=db_path)

    for i in range(30):
        journal.record_decision(symbol=f"SYM{i}/USDT", signal="HOLD", risk_approved=True, details={})
    for _ in range(1100):  # more than the old fixed fetch size (1000)
        journal.record_decision(
            symbol="SYM0/USDT", signal="HOLD", risk_approved=True, details={"paper": True, "candidate": "macd"}
        )

    snapshot = da.journal_snapshot(db_path, False)

    assert len(snapshot["decisions"]) == 30
    assert all(not d["details"].get("paper") for d in snapshot["decisions"])


def test_journal_snapshot_equity_uses_the_same_window_as_readiness_and_performance(tmp_path):
    """Regression: journal_snapshot's equity (feeds the dashboard's chart
    and 'PnL today'/'Return since first snapshot' stats) used to pass
    limit=2000 while real_portfolio_metrics/readiness_report used the
    default 5000 - once post-cutoff history exceeded 2000 rows, the
    Overview panel's 'first snapshot' would silently become a rolling
    window while Readiness still anchored to the true first one, so the
    two panels could disagree about what 'the start' means. All three must
    share the same window."""
    from datetime import datetime, timedelta, timezone

    db_path = str(tmp_path / "journal.db")
    journal = Journal(db_path=db_path)
    base = datetime(2026, 7, 10, tzinfo=timezone.utc)
    for i in range(2500):  # exceeds the old 2000 limit, well under 5000
        journal.record_equity_snapshot(
            capital=8000.0, total_equity=10000.0 + i, timestamp=(base + timedelta(minutes=i)).isoformat(),
            is_live=False,
        )

    snapshot = da.journal_snapshot(db_path, False)
    metrics = da.real_portfolio_metrics(db_path, False)

    assert snapshot["equity"][0]["timestamp"] == metrics["first_snapshot_timestamp"]


def test_clean_real_equity_curve_excludes_pre_cutoff_snapshots(tmp_path):
    """The accounting-fix cutoff: PortfolioAgent.reconcile_with_exchange
    used to count unexplained/faucet-seeded balances at full current
    market value as live equity - snapshots recorded before the fix
    reflect that incorrect accounting and must not blend into statistics
    that assume a continuous, consistent equity series."""
    db_path = str(tmp_path / "journal.db")
    journal = Journal(db_path=db_path)
    journal.record_equity_snapshot(capital=100000.0, total_equity=100916.0, timestamp="2026-07-09T18:09:11+00:00", is_live=False)
    journal.record_equity_snapshot(capital=8000.0, total_equity=10042.49, timestamp="2026-07-09T22:35:59+00:00", is_live=False)
    journal.record_equity_snapshot(capital=8000.0, total_equity=10100.0, timestamp="2026-07-09T23:00:00+00:00", is_live=False)

    cleaned = da._clean_real_equity_curve(journal, False)

    assert [s["total_equity"] for s in cleaned] == [10042.49, 10100.0]


def test_clean_trade_pnls_excludes_pre_cutoff_trades(tmp_path):
    """A second, later, separate accounting-fix cutoff:
    PortfolioManager.close_position used to credit the FULL sale proceeds
    of a reconciled/unexplained (faucet-seeded) position to realized PnL,
    instead of excluding the unexplained share - trades closed before the
    fix have a wrong pnl and must not feed forward-looking Sharpe/win-rate
    statistics (the 5 affected trade_audit rows were individually
    corrected via scripts/correct_trade_audit_unexplained_pnl.py; this
    cutoff protects the separate `trades` operational log instead)."""
    db_path = str(tmp_path / "journal.db")
    journal = Journal(db_path=db_path)
    journal.record_trade(symbol="BNB/USDT", side="sell", price=552.64, quantity=1.0, pnl=-18.44, timestamp="2026-07-09T23:53:09+00:00", is_live=False)
    journal.record_trade(symbol="XRP/USDT", side="sell", price=1.09, quantity=2267.0, pnl=-31.11, timestamp="2026-07-10T03:51:19+00:00", is_live=False)
    journal.record_trade(symbol="BTC/USDT", side="sell", price=65000.0, quantity=0.1, pnl=42.0, timestamp="2026-07-10T04:00:00+00:00", is_live=False)

    cleaned = da._clean_trade_pnls(journal, False)

    # order doesn't matter for these aggregate stats (unlike the equity
    # curve, which is a time series) - recent_trades returns newest-first
    assert sorted(cleaned) == sorted([-31.11, 42.0])


def test_clean_real_equity_curve_snapshot_exactly_at_cutoff_is_kept(tmp_path):
    db_path = str(tmp_path / "journal.db")
    journal = Journal(db_path=db_path)
    journal.record_equity_snapshot(
        capital=8000.0, total_equity=10042.49, timestamp=da._EQUITY_ACCOUNTING_FIX_CUTOFF, is_live=False
    )

    cleaned = da._clean_real_equity_curve(journal, False)

    assert len(cleaned) == 1


def test_real_portfolio_metrics_excludes_pre_cutoff_spike_from_drawdown_and_sharpe(tmp_path):
    """Regression for a real incident: blending the old (buggy, ~$100k
    inflated by faucet dust) and new (corrected, ~$10k) equity readings
    into one returns series produced a fabricated ~90% 'max drawdown' from
    the one-time restatement itself, not from any real trading event."""
    db_path = str(tmp_path / "journal.db")
    journal = Journal(db_path=db_path)
    journal.record_equity_snapshot(capital=100000.0, total_equity=100916.0, timestamp="2026-07-09T18:09:11+00:00", is_live=False)
    for i, value in enumerate([10042.49, 10050.0, 10060.0, 10055.0, 10070.0]):
        journal.record_equity_snapshot(
            capital=8000.0, total_equity=value, timestamp=f"2026-07-09T22:{36 + i}:00+00:00", is_live=False
        )

    metrics = da.real_portfolio_metrics(db_path, False)

    assert metrics["max_drawdown"] < 0.01  # small real fluctuation, not the ~90% fabricated one
    assert metrics["first_snapshot_timestamp"] == "2026-07-09T22:36:00+00:00"  # first post-cutoff snapshot


def test_readiness_report_elapsed_time_measured_from_post_cutoff_snapshot(tmp_path):
    db_path = str(tmp_path / "journal.db")
    journal = Journal(db_path=db_path)
    journal.record_equity_snapshot(capital=100000.0, total_equity=100916.0, timestamp="2020-01-01T00:00:00+00:00", is_live=False)
    journal.record_equity_snapshot(capital=8000.0, total_equity=10042.49, timestamp=da._EQUITY_ACCOUNTING_FIX_CUTOFF, is_live=False)

    from trading_assistant.config import ReadinessConfig

    report = da.readiness_report(db_path, ReadinessConfig(min_elapsed_days=0.0), False)

    elapsed_criterion = next(c for c in report["criteria"] if c["name"] == "Elapsed operating time")
    # The measured duration must follow the post-cutoff snapshot as the
    # calendar advances; a fixed 30-day upper bound expires over time.
    from datetime import datetime, timezone

    assert "day(s)" in elapsed_criterion["detail"]
    days_str = elapsed_criterion["detail"].split(" day(s)")[0]
    expected_days = (datetime.now(timezone.utc) - datetime.fromisoformat(
        da._EQUITY_ACCOUNTING_FIX_CUTOFF)).total_seconds() / 86400
    assert abs(float(days_str) - expected_days) < 0.2


def test_journal_snapshot_equity_excludes_pre_cutoff_snapshots(tmp_path):
    """Regression: the dashboard's live equity chart and 'PnL today' stat
    read journal_snapshot()['equity'] directly - unfiltered, this showed a
    fabricated ~$90k crash at the exact moment the accounting fix landed
    (the Nairobi calendar day spans the UTC cutoff, so 'PnL today' could
    diff a clean snapshot against a pre-fix one from the same EAT day)."""
    db_path = str(tmp_path / "journal.db")
    journal = Journal(db_path=db_path)
    journal.record_equity_snapshot(capital=100000.0, total_equity=100474.51, timestamp="2026-07-09T21:48:11+00:00", is_live=False)
    journal.record_equity_snapshot(capital=8000.0, total_equity=10042.49, timestamp="2026-07-09T22:35:59+00:00", is_live=False)

    snapshot = da.journal_snapshot(db_path, False)

    assert len(snapshot["equity"]) == 1
    assert snapshot["equity"][0]["total_equity"] == 10042.49


def test_journal_snapshot_excludes_testnet_trades_when_viewing_live(tmp_path):
    """Regression for a real incident: a stale testnet-era trade (e.g. an
    ARB/USDT buy filled with fake money before the system ever switched to
    `mode: live`) blended into the live dashboard's "Recent trades"/PnL
    figures because trades had no way to record which mode produced them.
    Once tagged, journal_snapshot(is_live=True) must show only real trades,
    and vice versa."""
    db_path = str(tmp_path / "journal.db")
    journal = Journal(db_path=db_path)
    journal.record_trade(symbol="ARB/USDT", side="buy", price=0.0951, quantity=1051.5, pnl=None, is_live=False)
    journal.record_trade(symbol="BTC/USDT", side="buy", price=65000.0, quantity=0.0001, pnl=None, is_live=True)

    live_snapshot = da.journal_snapshot(db_path, True)
    testnet_snapshot = da.journal_snapshot(db_path, False)

    assert [t["symbol"] for t in live_snapshot["trades"]] == ["BTC/USDT"]
    assert [t["symbol"] for t in testnet_snapshot["trades"]] == ["ARB/USDT"]


def test_journal_snapshot_equity_excludes_the_other_modes_snapshots(tmp_path):
    """Same bug, for equity/PnL: a leftover testnet equity baseline (e.g. an
    old $1000 fictional bankroll) must not blend into the live equity curve
    driving 'Today's PnL'/'Return since first snapshot', or the reverse."""
    db_path = str(tmp_path / "journal.db")
    journal = Journal(db_path=db_path)
    journal.record_equity_snapshot(capital=1000.0, total_equity=1000.0, timestamp="2026-07-10T10:00:00+00:00", is_live=False)
    journal.record_equity_snapshot(capital=7.70, total_equity=7.70, timestamp="2026-07-10T13:00:00+00:00", is_live=True)

    live_snapshot = da.journal_snapshot(db_path, True)
    testnet_snapshot = da.journal_snapshot(db_path, False)

    assert [s["total_equity"] for s in live_snapshot["equity"]] == [7.70]
    assert [s["total_equity"] for s in testnet_snapshot["equity"]] == [1000.0]


def test_real_portfolio_metrics_does_not_mix_testnet_and_live_equity(tmp_path):
    db_path = str(tmp_path / "journal.db")
    journal = Journal(db_path=db_path)
    journal.record_equity_snapshot(capital=1000.0, total_equity=1000.0, timestamp="2026-07-10T10:00:00+00:00", is_live=False)
    journal.record_equity_snapshot(capital=7.70, total_equity=7.70, timestamp="2026-07-10T13:00:00+00:00", is_live=True)
    journal.record_equity_snapshot(capital=7.70, total_equity=8.10, timestamp="2026-07-10T14:00:00+00:00", is_live=True)

    metrics = da.real_portfolio_metrics(db_path, True)

    # anchored to the first LIVE snapshot ($7.70), not the stale testnet
    # $1000 baseline, which would otherwise fabricate a ~99% "loss"
    assert metrics["first_snapshot_timestamp"] == "2026-07-10T13:00:00+00:00"


def test_trade_recorded_without_is_live_is_treated_as_testnet_not_invisible(tmp_path):
    """A trade inserted via record_trade with is_live left as None (the
    parameter's default) stores SQL NULL. It must land in the testnet view,
    not vanish from both, and never leak into the live view.

    Mechanism note: dashboard reads used to depend on Journal._migrate's
    NULL->0 backfill running as a construction side effect - but dashboard
    journals are now PURE READERS (initialize=False, no migrations), so the
    NULL-is-testnet rule lives in the read filter itself
    (data_access._matches_mode). This test is what guards that a NULL row
    can never become invisible in both views again."""
    db_path = str(tmp_path / "journal.db")
    journal = Journal(db_path=db_path)
    journal.record_trade(symbol="ETH/USDT", side="buy", price=3000.0, quantity=0.01, pnl=None)

    assert da.journal_snapshot(db_path, True)["trades"] == []
    assert [t["symbol"] for t in da.journal_snapshot(db_path, False)["trades"]] == ["ETH/USDT"]


def test_drift_status_returns_latest_event_per_symbol_and_skips_symbols_with_none(tmp_path):
    db_path = str(tmp_path / "journal.db")
    journal = Journal(db_path=db_path)

    journal.record_drift_event(symbol="BTC/USDT", overall_severity="none", summary={"overall_severity": "none"})
    journal.record_drift_event(
        symbol="BTC/USDT", overall_severity="significant", summary={"overall_severity": "significant"}
    )
    # ETH/USDT has no drift events at all - no model promoted for it yet.

    rows = da.drift_status(db_path, ["BTC/USDT", "ETH/USDT"])

    assert len(rows) == 1
    assert rows[0]["symbol"] == "BTC/USDT"
    assert rows[0]["overall_severity"] == "significant"  # the latest, not the first


def test_read_events_rejects_table_names_outside_the_allowlist(tmp_path):
    """Hardening: `table` is interpolated directly into the SQL text since
    sqlite3 has no parameterized-identifier syntax. Every current caller
    passes a hardcoded literal, but that's "safe by accident" - a future
    caller passing a request-derived table name would be SQL injection
    with no visible warning without this allowlist check."""
    db_path = str(tmp_path / "journal.db")
    Journal(db_path=db_path)

    with pytest.raises(ValueError):
        da._read_events(db_path, "sqlite_master")
    with pytest.raises(ValueError):
        da._read_events(db_path, "decisions; DROP TABLE decisions; --")


def test_read_events_allows_known_event_tables(tmp_path):
    db_path = str(tmp_path / "journal.db")
    Journal(db_path=db_path)

    assert da._read_events(db_path, "retrain_events") == []
    assert da._read_events(db_path, "tournament_events") == []


def test_backtest_live_correlation_records_pairs_tournament_report_with_paper_snapshots(tmp_path):
    db_path = str(tmp_path / "journal.db")
    journal = Journal(db_path=db_path)

    journal.record_tournament_event(
        symbol="BTC/USDT",
        winners={},
        report={
            "candidates": [
                {"name": "macd", "by_regime": {"trending_up": {"median_sharpe": 1.2, "num_folds": 4}}},
            ]
        },
    )
    for i in range(6):
        journal.record_paper_equity_snapshot(
            symbol="BTC/USDT", candidate="macd", capital=9000.0, total_equity=10000.0 + i * 10, num_trades=i + 5
        )

    records = da.backtest_live_correlation_records(db_path, ["BTC/USDT"], min_live_trades=5)

    assert len(records) == 1
    assert records[0]["symbol"] == "BTC/USDT"
    assert records[0]["candidate"] == "macd"
    assert records[0]["backtest_sharpe"] == 1.2
    assert records[0]["live_trades"] == 10  # last snapshot's num_trades


def test_backtest_live_correlation_records_excludes_thin_live_history(tmp_path):
    db_path = str(tmp_path / "journal.db")
    journal = Journal(db_path=db_path)

    journal.record_tournament_event(
        symbol="BTC/USDT",
        winners={},
        report={"candidates": [{"name": "macd", "by_regime": {"trending_up": {"median_sharpe": 1.2, "num_folds": 4}}}]},
    )
    journal.record_paper_equity_snapshot(
        symbol="BTC/USDT", candidate="macd", capital=9000.0, total_equity=10000.0, num_trades=1
    )

    records = da.backtest_live_correlation_records(db_path, ["BTC/USDT"], min_live_trades=5)

    assert records == []


def test_survivorship_bias_records_flags_champion_below_the_unbiased_average(tmp_path):
    db_path = str(tmp_path / "journal.db")
    journal = Journal(db_path=db_path)

    journal.record_tournament_event(
        symbol="BTC/USDT",
        winners={"trending_up": {"regime": "trending_up", "winner": "macd", "reason": "cleared the bar"}},
        report={
            "candidates": [
                {"name": "macd", "by_regime": {"trending_up": {"median_sharpe": 2.0, "num_folds": 5}}},
                {"name": "sma_crossover", "by_regime": {"trending_up": {"median_sharpe": 0.5, "num_folds": 5}}},
            ]
        },
    )
    for i in range(6):
        journal.record_paper_equity_snapshot(
            symbol="BTC/USDT", candidate="macd", capital=9000.0, total_equity=9000.0 - i, num_trades=i + 5
        )

    records = da.survivorship_bias_records(db_path, ["BTC/USDT"], min_live_trades=5)

    assert len(records) == 1
    assert records[0]["symbol"] == "BTC/USDT"
    assert records[0]["champion"] == "macd"
    assert records[0]["avg_candidate_backtest_sharpe"] == 1.25  # (2.0 + 0.5) / 2, not just macd's 2.0
    assert records[0]["beats_baseline"] is False  # a shrinking paper equity curve is a negative live Sharpe


def test_recent_alerts_returns_persisted_alerts_in_chronological_order(tmp_path):
    db_path = str(tmp_path / "journal.db")
    journal = Journal(db_path=db_path)

    journal.record_alert(category="a", severity="info", message="first")
    journal.record_alert(category="b", severity="critical", message="second")

    rows = da.recent_alerts(db_path)

    assert [r["message"] for r in rows] == ["first", "second"]


def test_execution_quality_summary_aggregates_per_symbol(tmp_path):
    db_path = str(tmp_path / "journal.db")
    journal = Journal(db_path=db_path)

    journal.record_execution_quality(
        symbol="BTC/USDT", side="buy", intended_price=100.0, fill_price=101.0, slippage_pct=0.01, latency_ms=40.0
    )
    journal.record_execution_quality(
        symbol="BTC/USDT", side="sell", intended_price=100.0, fill_price=99.0, slippage_pct=0.01, latency_ms=60.0
    )
    journal.record_execution_quality(
        symbol="ETH/USDT", side="buy", intended_price=50.0, fill_price=50.0, slippage_pct=0.0, latency_ms=30.0
    )

    summary = da.execution_quality_summary(db_path)

    by_symbol = {row["symbol"]: row for row in summary["by_symbol"]}
    assert by_symbol["BTC/USDT"]["num_orders"] == 2
    assert by_symbol["BTC/USDT"]["avg_slippage_pct"] == pytest.approx(0.01)
    assert by_symbol["ETH/USDT"]["num_orders"] == 1


def test_trade_audit_records_returns_most_recent_first(tmp_path):
    db_path = str(tmp_path / "journal.db")
    journal = Journal(db_path=db_path)
    journal.record_trade_audit(symbol="BTC/USDT", side="long", exit_price=100.0, quantity=1.0)
    journal.record_trade_audit(symbol="BTC/USDT", side="long", exit_price=110.0, quantity=1.0)

    records = da.trade_audit_records(db_path)

    assert len(records) == 2
    assert records[0]["exit_price"] == 110.0  # most recent first for the dashboard
    assert records[1]["exit_price"] == 100.0


def test_system_health_no_heartbeat_returns_unknown_not_fabricated(tmp_path):
    db_path = str(tmp_path / "journal.db")
    Journal(db_path=db_path)  # just initializes the schema, no heartbeat rows

    health = da.system_health(db_path, poll_interval_seconds=60)

    assert health["score"] is None
    assert health["label"] == "unknown"
    assert health["uptime_seconds"] is None


def test_system_health_healthy_with_a_fresh_heartbeat(tmp_path):
    db_path = str(tmp_path / "journal.db")
    journal = Journal(db_path=db_path)
    journal.record_heartbeat_start()
    journal.record_heartbeat_cycle(cycle_number=0, status="ok")

    health = da.system_health(db_path, poll_interval_seconds=60)

    assert health["label"] == "healthy"
    assert health["score"] == 100
    assert health["uptime_seconds"] is not None and health["uptime_seconds"] >= 0
    assert health["last_heartbeat_status"] == "ok"


def test_system_health_reflects_tripped_circuit_breaker(tmp_path):
    db_path = str(tmp_path / "journal.db")
    journal = Journal(db_path=db_path)
    journal.record_heartbeat_start()
    journal.record_heartbeat_cycle(cycle_number=0, status="ok")
    journal.record_circuit_breaker_event(tripped=True, reason="daily loss limit breached")

    health = da.system_health(db_path, poll_interval_seconds=60)

    assert health["label"] == "critical"
    assert health["circuit_breaker_tripped"] is True


def test_system_health_reflects_staleness(tmp_path):
    db_path = str(tmp_path / "journal.db")
    journal = Journal(db_path=db_path)
    journal.record_heartbeat_start(timestamp="2020-01-01T00:00:00+00:00")
    journal.record_heartbeat_cycle(cycle_number=0, status="ok", timestamp="2020-01-01T00:00:00+00:00")

    health = da.system_health(db_path, poll_interval_seconds=60)

    assert health["label"] in ("degraded", "critical")
    assert health["staleness_seconds"] > 180  # far beyond 3x the 60s poll interval


def test_system_health_uses_the_fresh_restart_not_a_stale_pre_crash_cycle(tmp_path):
    """Regression: staleness used to be measured from last_cycle_at
    whenever it existed AT ALL (`last_cycle_at or started_at`), even if
    that cycle predates a crash/restart. record_heartbeat_start() writes a
    fresh row immediately on restart, but the latest cycle row only
    updates once the first post-restart cycle finishes - which can take
    the whole warm-start window. A fresh restart with an old leftover
    cycle row must report staleness from the FRESH start, not the stale
    cycle, or the dashboard falsely shows degraded/critical health while
    the system is healthy and still warming up."""
    db_path = str(tmp_path / "journal.db")
    journal = Journal(db_path=db_path)
    journal.record_heartbeat_cycle(cycle_number=99, status="ok", timestamp="2020-01-01T00:00:00+00:00")
    journal.record_heartbeat_start()  # fresh, right now

    health = da.system_health(db_path, poll_interval_seconds=300)

    assert health["label"] == "healthy"
    assert health["staleness_seconds"] < 5


def test_system_health_reflects_recent_error_rate(tmp_path):
    db_path = str(tmp_path / "journal.db")
    journal = Journal(db_path=db_path)
    journal.record_heartbeat_start()
    for i in range(4):
        journal.record_heartbeat_cycle(cycle_number=i, status="error")

    health = da.system_health(db_path, poll_interval_seconds=60)

    assert health["recent_error_rate"] == 1.0
    assert health["score"] < 100
