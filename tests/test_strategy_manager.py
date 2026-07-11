from datetime import datetime

import pandas as pd

from trading_assistant.agents.features import build_features, label_next_bar_direction
from trading_assistant.agents.strategy_manager import (
    REGIMES,
    CandidateReport,
    FoldRecord,
    RegimePerformance,
    RegimeSelection,
    StrategyCandidate,
    StrategyManager,
    TournamentResult,
    default_candidates,
)
from trading_assistant.backtesting.framework import BacktestEngineV2, OrderDirection
from trading_assistant.strategies.base import Signal
from trading_assistant.strategies.implementations import SMACrossoverStrategy


def _uptrend_ohlcv(n=300, start=100.0, step=0.5):
    closes = [start + i * step for i in range(n)]
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC"),
            "open": closes,
            "high": [c + 1 for c in closes],
            "low": [c - 1 for c in closes],
            "close": closes,
            "volume": [100] * n,
        }
    )


def _downtrend_ohlcv(n=300, start=200.0, step=0.5):
    closes = [start - i * step for i in range(n)]
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC"),
            "open": closes,
            "high": [c + 1 for c in closes],
            "low": [c - 1 for c in closes],
            "close": closes,
            "volume": [100] * n,
        }
    )


def test_default_candidates_includes_ml_and_every_rule_based_strategy():
    candidates = default_candidates()
    names = {c.name for c in candidates}
    kinds = {c.name: c.kind for c in candidates}

    assert names == {
        "ml_ensemble", "sma_crossover", "rsi_mean_reversion", "macd", "bollinger_bands", "candlestick_patterns",
        "time_series_momentum", "smart_money_concepts",
    }
    assert kinds["ml_ensemble"] == "ml"
    assert kinds["sma_crossover"] == "rule"
    assert kinds["rsi_mean_reversion"] == "rule"
    assert kinds["macd"] == "rule"
    assert kinds["bollinger_bands"] == "rule"
    assert kinds["candlestick_patterns"] == "rule"


def test_aggregate_by_regime_computes_median_sharpe_and_consistency():
    manager = StrategyManager()
    folds = [
        FoldRecord(0, "trending_up", {"sharpe_ratio": 1.0, "total_return_pct": 2.0, "num_trades": 3}),
        FoldRecord(1, "trending_up", {"sharpe_ratio": 2.0, "total_return_pct": -1.0, "num_trades": 2}),
        FoldRecord(2, "ranging", {"sharpe_ratio": 0.5, "total_return_pct": 1.0, "num_trades": 1}),
    ]

    by_regime = manager._aggregate_by_regime(folds)

    assert by_regime["trending_up"].num_folds == 2
    assert by_regime["trending_up"].median_sharpe == 1.5
    assert by_regime["trending_up"].pct_folds_profitable == 0.5  # 1 of 2 folds had a positive return
    assert by_regime["trending_up"].total_trades == 5
    assert by_regime["ranging"].num_folds == 1
    assert by_regime["trending_down"].num_folds == 0


def test_select_winners_picks_highest_median_sharpe_candidate_that_clears_the_bar():
    manager = StrategyManager(min_folds_required=2, min_sharpe_to_promote=0.5, min_pct_folds_profitable=0.5)

    report_a = CandidateReport(
        name="a",
        kind="rule",
        by_regime={
            "trending_up": RegimePerformance("trending_up", num_folds=3, median_sharpe=1.2, pct_folds_profitable=1.0, total_trades=5),
            "trending_down": RegimePerformance("trending_down"),
            "ranging": RegimePerformance("ranging", num_folds=3, median_sharpe=0.1, pct_folds_profitable=0.3, total_trades=2),
        },
    )
    report_b = CandidateReport(
        name="b",
        kind="ml",
        by_regime={
            "trending_up": RegimePerformance("trending_up", num_folds=3, median_sharpe=0.8, pct_folds_profitable=1.0, total_trades=4),
            "trending_down": RegimePerformance("trending_down"),
            "ranging": RegimePerformance("ranging", num_folds=3, median_sharpe=0.6, pct_folds_profitable=0.7, total_trades=3),
        },
    )

    winners = manager._select_winners("BTC/USDT", [report_a, report_b])

    assert winners["trending_up"].winner == "a"  # higher sharpe (1.2 > 0.8), both clear the bar
    assert winners["ranging"].winner == "b"  # a fails sharpe (0.1) and consistency (0.3); b clears both
    assert winners["trending_down"].winner is None  # neither candidate has any folds in this regime
    assert "no candidate had any walk-forward folds" in winners["trending_down"].reason


def test_select_winners_returns_none_when_nothing_clears_the_bar():
    manager = StrategyManager(min_folds_required=2, min_sharpe_to_promote=5.0, min_pct_folds_profitable=0.9)
    report = CandidateReport(
        name="a",
        kind="rule",
        by_regime={
            "trending_up": RegimePerformance("trending_up", num_folds=3, median_sharpe=0.5, pct_folds_profitable=0.5, total_trades=2),
            "trending_down": RegimePerformance("trending_down"),
            "ranging": RegimePerformance("ranging"),
        },
    )

    winners = manager._select_winners("BTC/USDT", [report])

    assert all(selection.winner is None for selection in winners.values())
    # trending_up had folds but none cleared the bar - distinct reason from "no folds at all"
    assert "did not clear" in winners["trending_up"].reason or "no candidate cleared" in winners["trending_up"].reason
    assert "no candidate had any walk-forward folds" in winners["trending_down"].reason


def _report(name, sharpe, num_folds=3, pct_profitable=1.0):
    return CandidateReport(
        name=name,
        kind="rule",
        by_regime={
            "trending_up": RegimePerformance(
                "trending_up", num_folds=num_folds, median_sharpe=sharpe, pct_folds_profitable=pct_profitable, total_trades=5
            ),
            "trending_down": RegimePerformance("trending_down"),
            "ranging": RegimePerformance("ranging"),
        },
    )


class TestChampionSwitchHysteresis:
    def test_no_incumbent_picks_the_best_candidate_normally(self):
        manager = StrategyManager(
            min_folds_required=2, min_sharpe_to_promote=0.5, min_pct_folds_profitable=0.5,
            champion_switch_min_runs=3, champion_switch_margin=0.1,
        )
        winners = manager._select_winners("BTC/USDT", [_report("a", 0.6), _report("b", 1.0)])
        assert winners["trending_up"].winner == "b"

    def test_incumbent_retained_when_held_too_briefly_even_with_a_real_edge(self):
        manager = StrategyManager(
            min_folds_required=2, min_sharpe_to_promote=0.5, min_pct_folds_profitable=0.5,
            champion_switch_min_runs=3, champion_switch_margin=0.1,
        )
        # "a" has been champion for only 1 run so far.
        manager.winner_history["BTC/USDT"] = {"trending_up": ["a"], "trending_down": [], "ranging": []}

        winners = manager._select_winners("BTC/USDT", [_report("a", 0.6), _report("b", 1.0)])  # b's edge (0.4) clears the margin

        assert winners["trending_up"].winner == "a"  # retained - held for only 1 run, needs 3
        assert "retained" in winners["trending_up"].reason

    def test_incumbent_replaced_once_held_long_enough_and_edge_clears_margin(self):
        manager = StrategyManager(
            min_folds_required=2, min_sharpe_to_promote=0.5, min_pct_folds_profitable=0.5,
            champion_switch_min_runs=3, champion_switch_margin=0.1,
        )
        manager.winner_history["BTC/USDT"] = {"trending_up": ["a", "a", "a"], "trending_down": [], "ranging": []}

        winners = manager._select_winners("BTC/USDT", [_report("a", 0.6), _report("b", 1.0)])

        assert winners["trending_up"].winner == "b"

    def test_incumbent_retained_when_held_long_enough_but_edge_is_only_noise(self):
        manager = StrategyManager(
            min_folds_required=2, min_sharpe_to_promote=0.5, min_pct_folds_profitable=0.5,
            champion_switch_min_runs=3, champion_switch_margin=0.1,
        )
        manager.winner_history["BTC/USDT"] = {"trending_up": ["a", "a", "a"], "trending_down": [], "ranging": []}

        # b's edge over a is only 0.05 - below the 0.1 switch margin.
        winners = manager._select_winners("BTC/USDT", [_report("a", 0.6), _report("b", 0.65)])

        assert winners["trending_up"].winner == "a"

    def test_incumbent_no_longer_qualifying_is_replaced_immediately(self):
        """Hysteresis protects a genuinely still-qualifying incumbent from
        noise - it must never keep a candidate that's fallen below the
        promotion bar itself in place just because it "held the seat" recently."""
        manager = StrategyManager(
            min_folds_required=2, min_sharpe_to_promote=0.5, min_pct_folds_profitable=0.5,
            champion_switch_min_runs=3, champion_switch_margin=0.1,
        )
        manager.winner_history["BTC/USDT"] = {"trending_up": ["a", "a", "a"], "trending_down": [], "ranging": []}

        # a's own sharpe has now fallen below the promotion bar (0.5).
        winners = manager._select_winners("BTC/USDT", [_report("a", 0.3), _report("b", 0.6)])

        assert winners["trending_up"].winner == "b"

    def test_consecutive_runs_held_counts_only_the_unbroken_trailing_streak(self):
        manager = StrategyManager()
        manager.winner_history["BTC/USDT"] = {"trending_up": ["a", "b", "a", "a", "a"], "trending_down": [], "ranging": []}

        assert manager._consecutive_runs_held("BTC/USDT", "trending_up", "a") == 3  # only the trailing run of a's
        assert manager._consecutive_runs_held("BTC/USDT", "trending_up", "b") == 0  # b isn't the most recent winner

    def test_default_thresholds_are_reasonably_conservative(self):
        """Sanity check on the defaults themselves, not just the mechanism -
        a fresh StrategyManager should require more than a single run and
        more than an epsilon improvement before ever switching."""
        manager = StrategyManager()
        assert manager.champion_switch_min_runs >= 2
        assert manager.champion_switch_margin > 0


def test_winner_for_returns_none_before_any_tournament_has_run():
    manager = StrategyManager()
    assert manager.winner_for("BTC/USDT", "trending_up") is None


class RecordingStrategy:
    """Captures how many prices it was shown on each call - used to verify
    the rule-based backtest gives a growing history, not just the isolated
    test window."""

    def __init__(self):
        self.seen_lengths = []
        self.saw_ohlc = []

    def generate_signal(self, data):
        prices = data["prices"]
        self.seen_lengths.append(len(prices))
        self.saw_ohlc.append(all(k in data for k in ("open", "high", "low", "close")))
        return Signal.HOLD


def test_backtest_rule_passes_full_ohlc_when_provided():
    """Candlestick-pattern-based strategies need open/high/low, not just
    close - _backtest_rule must forward all four when the tournament calls
    it (run_tournament), while staying backward compatible for direct
    3-series callers that only care about close."""
    manager = StrategyManager(train_window_bars=60, test_window_bars=20)
    ohlcv = _uptrend_ohlcv(n=300)

    featured = build_features(ohlcv)
    featured["target"] = label_next_bar_direction(featured)
    featured = featured.dropna()
    test_slice = featured.iloc[60:80]

    recorder = RecordingStrategy()
    manager._backtest_rule(
        recorder, "BTC/USDT", ohlcv["close"], test_slice,
        full_open=ohlcv["open"], full_high=ohlcv["high"], full_low=ohlcv["low"],
    )

    assert recorder.saw_ohlc and all(recorder.saw_ohlc)


def test_run_tournament_includes_candlestick_pattern_candidate_without_error():
    manager = StrategyManager(train_window_bars=60, test_window_bars=20, min_folds_required=1)
    ohlcv = _uptrend_ohlcv(n=300)

    result = manager.run_tournament("BTC/USDT", ohlcv)

    candidate_names = {report.name for report in result.candidates}
    assert "candlestick_patterns" in candidate_names


def test_backtest_rule_gives_strategy_full_price_history_up_to_each_bar():
    manager = StrategyManager(train_window_bars=60, test_window_bars=20)
    ohlcv = _uptrend_ohlcv(n=300)
    full_close = ohlcv["close"]

    featured = build_features(ohlcv)
    featured["target"] = label_next_bar_direction(featured)
    featured = featured.dropna()
    test_slice = featured.iloc[60:80]  # first fold's test window: absolute rows 60-79

    recorder = RecordingStrategy()
    manager._backtest_rule(recorder, "BTC/USDT", full_close, test_slice)

    # the strategy should see everything up to and including each bar - not
    # just the 20-bar test window in isolation (which would starve
    # SMA/RSI/MACD of the warm-up history they need). test_slice's own index
    # labels are the true absolute row positions (build_features' warm-up
    # dropna() shifts them, so don't hardcode an assumed absolute offset).
    expected_first_length = int(test_slice.index[0]) + 1
    expected_last_length = int(test_slice.index[-1]) + 1

    assert recorder.seen_lengths[0] == expected_first_length
    assert recorder.seen_lengths[-1] == expected_last_length
    assert recorder.seen_lengths == sorted(recorder.seen_lengths)  # strictly growing
    assert recorder.seen_lengths[0] > 20  # more than just the isolated 20-bar test window


def test_tournament_backtest_reflects_configured_commission_rate():
    """Regression: BacktestEngineV2 always used its own hardcoded
    commission/slippage defaults (0.001/0.0005), never
    tournament.paper_commission_rate - the tournament's own backtest
    validation and ChampionChallengerAgent's paper trading must share the
    same friction assumption, or a candidate could look better in one than
    the other purely from a cost-basis mismatch, not real performance."""
    ohlcv = _uptrend_ohlcv(n=300)
    common = dict(train_window_bars=60, test_window_bars=20, min_folds_required=2, min_sharpe_to_promote=-100.0)
    zero_friction = StrategyManager(**common, commission_rate=0.0, slippage_rate=0.0)
    high_friction = StrategyManager(**common, commission_rate=0.05, slippage_rate=0.0)  # large, obvious 5% fee

    zero_result = zero_friction.run_tournament("BTC/USDT", ohlcv)
    high_result = high_friction.run_tournament("BTC/USDT", ohlcv)

    zero_sharpes = [f.metrics["sharpe_ratio"] for c in zero_result.candidates for f in c.folds]
    high_sharpes = [f.metrics["sharpe_ratio"] for c in high_result.candidates for f in c.folds]

    assert sum(high_sharpes) < sum(zero_sharpes)


def test_run_tournament_end_to_end_with_real_candidates_on_synthetic_data():
    manager = StrategyManager(
        train_window_bars=60,
        test_window_bars=20,
        min_folds_required=2,
        min_sharpe_to_promote=-100.0,
        min_pct_folds_profitable=0.0,
    )
    ohlcv = _uptrend_ohlcv(n=300)

    result = manager.run_tournament("BTC/USDT", ohlcv)

    assert {c.name for c in result.candidates} == {
        "ml_ensemble",
        "sma_crossover",
        "rsi_mean_reversion",
        "macd",
        "bollinger_bands",
        "candlestick_patterns",
        "time_series_momentum",
        "smart_money_concepts",
    }
    for candidate in result.candidates:
        assert set(candidate.by_regime.keys()) == {"trending_up", "trending_down", "ranging"}
        assert len(candidate.folds) >= 2

    # a permissive-enough bar should promote at least one candidate for the
    # dominant regime in a clean, strong uptrend
    assert manager.winner_for("BTC/USDT", "trending_up") is not None


def test_run_tournament_journals_the_result(tmp_path):
    from trading_assistant.persistence.journal import Journal

    journal = Journal(db_path=str(tmp_path / "journal.db"))
    manager = StrategyManager(
        train_window_bars=60, test_window_bars=20, min_folds_required=2, journal=journal
    )
    ohlcv = _uptrend_ohlcv(n=300)

    manager.run_tournament("BTC/USDT", ohlcv)

    latest = journal.latest_tournament_event("BTC/USDT")
    assert latest is not None
    assert latest["symbol"] == "BTC/USDT"


def test_ensure_direction_opens_flips_and_is_idempotent():
    manager = StrategyManager(candidates=[])
    engine = BacktestEngineV2(initial_capital=10000.0)
    ts = datetime(2026, 1, 1)

    # flat -> long
    position = manager._ensure_direction(engine, "BTC/USDT", OrderDirection.LONG, None, 100.0, ts)
    assert position is not None and position.direction == OrderDirection.LONG

    # long -> long again: idempotent, same position retained (no close/reopen)
    same_position = manager._ensure_direction(engine, "BTC/USDT", OrderDirection.LONG, position, 101.0, ts)
    assert same_position is position

    # long -> short: closes the long, opens a short
    short_position = manager._ensure_direction(engine, "BTC/USDT", OrderDirection.SHORT, position, 102.0, ts)
    assert short_position is not None and short_position.direction == OrderDirection.SHORT


def test_flatten_if_long_closes_long_but_leaves_a_short_untouched():
    manager = StrategyManager(candidates=[])
    engine = BacktestEngineV2(initial_capital=10000.0)
    ts = datetime(2026, 1, 1)

    long_position = manager._ensure_direction(engine, "BTC/USDT", OrderDirection.LONG, None, 100.0, ts)
    flattened = StrategyManager._flatten_if_long(engine, long_position, 101.0, ts)
    assert flattened is None

    engine2 = BacktestEngineV2(initial_capital=10000.0)
    short_position = manager._ensure_direction(engine2, "BTC/USDT", OrderDirection.SHORT, None, 100.0, ts)
    unchanged = StrategyManager._flatten_if_long(engine2, short_position, 101.0, ts)
    assert unchanged is short_position  # a long-only SELL handler must never touch an existing short


def test_ensure_direction_uses_risk_based_sizing_when_configured():
    """Parity fix: when risk_per_trade is configured, backtest sizing must
    use the SAME risk_per_trade/stop-distance formula live trading uses,
    not the old flat ~95%-of-capital assumption."""
    manager = StrategyManager(
        candidates=[], risk_per_trade=0.02, position_stop_loss_pct=0.01, max_position_size=0.1
    )
    engine = BacktestEngineV2(initial_capital=10000.0)
    ts = datetime(2026, 1, 1)

    position = manager._ensure_direction(engine, "BTC/USDT", OrderDirection.LONG, None, 100.0, ts)

    assert position is not None
    # risk_amount = 10000*0.02 = 200; notional = 200/0.01 = 20000, capped at
    # 10000*0.1 = 1000 -> quantity = 10.0, a small fraction of the old
    # (10000*0.95)/100 = 95.0 the flat-sizing default would have opened.
    assert position.quantity == 10.0


def test_ensure_direction_keeps_old_flat_sizing_by_default():
    """Backward compatible: risk_per_trade defaults to None, so existing
    callers/tests see exactly the old flat ~95%-of-capital behavior."""
    manager = StrategyManager(candidates=[])
    engine = BacktestEngineV2(initial_capital=10000.0)
    ts = datetime(2026, 1, 1)

    position = manager._ensure_direction(engine, "BTC/USDT", OrderDirection.LONG, None, 100.0, ts)

    assert position is not None
    assert position.quantity == 95.0


def test_long_only_mode_cannot_profit_from_a_clean_downtrend():
    """A downward SMA crossover in a clean downtrend never satisfies the
    BUY condition, so a long-only backtest should never open a position at
    all - it can neither profit from nor be hurt by the decline."""
    manager = StrategyManager(
        candidates=[StrategyCandidate("sma_crossover", "rule", SMACrossoverStrategy)],
        train_window_bars=60,
        test_window_bars=20,
        min_folds_required=2,
        allow_short=False,
    )
    ohlcv = _downtrend_ohlcv(n=300)

    result = manager.run_tournament("BTC/USDT", ohlcv)

    total_trades = sum(f.metrics["num_trades"] for f in result.candidates[0].folds)
    assert total_trades == 0


def test_short_selling_captures_profit_in_a_downtrend_that_long_only_misses():
    """Regression/validation test for the staged short-selling rollout: the
    SAME strategy on the SAME downtrend data must be able to open (and
    profit from) short positions once allow_short=True, where the
    long-only backtest above found nothing to trade at all."""
    manager = StrategyManager(
        candidates=[StrategyCandidate("sma_crossover", "rule", SMACrossoverStrategy)],
        train_window_bars=60,
        test_window_bars=20,
        min_folds_required=2,
        allow_short=True,
    )
    ohlcv = _downtrend_ohlcv(n=300)

    result = manager.run_tournament("BTC/USDT", ohlcv)

    folds = result.candidates[0].folds
    total_trades = sum(f.metrics["num_trades"] for f in folds)
    total_return = sum(f.metrics["total_return_pct"] for f in folds)

    assert total_trades > 0  # downward crossovers now open short positions
    assert total_return > 0  # a short position in a clean downtrend should be profitable


def test_compare_long_only_vs_long_short_runs_both_and_restores_original_setting():
    manager = StrategyManager(
        candidates=[StrategyCandidate("sma_crossover", "rule", SMACrossoverStrategy)],
        train_window_bars=60,
        test_window_bars=20,
        min_folds_required=2,
        allow_short=False,
    )
    ohlcv = _downtrend_ohlcv(n=300)

    comparison = manager.compare_long_only_vs_long_short("BTC/USDT", ohlcv)

    long_only_trades = sum(f.metrics["num_trades"] for f in comparison["long_only"].candidates[0].folds)
    long_short_trades = sum(f.metrics["num_trades"] for f in comparison["long_short"].candidates[0].folds)

    assert long_only_trades == 0
    assert long_short_trades > 0
    assert manager.allow_short is False  # restored to the original setting afterward


def test_stability_score_is_none_before_two_runs_have_completed():
    manager = StrategyManager(train_window_bars=60, test_window_bars=20, min_folds_required=2)
    assert manager.stability_score("BTC/USDT", "trending_up") is None

    manager.run_tournament("BTC/USDT", _uptrend_ohlcv(n=300))
    assert manager.stability_score("BTC/USDT", "trending_up") is None  # only one run so far


def test_stability_score_is_one_when_the_same_winner_repeats():
    manager = StrategyManager(
        candidates=[StrategyCandidate("sma_crossover", "rule", SMACrossoverStrategy)],
        train_window_bars=60,
        test_window_bars=20,
        min_folds_required=2,
        min_sharpe_to_promote=-100.0,
        min_pct_folds_profitable=0.0,
    )
    ohlcv = _uptrend_ohlcv(n=300)

    manager.run_tournament("BTC/USDT", ohlcv)
    manager.run_tournament("BTC/USDT", ohlcv)
    manager.run_tournament("BTC/USDT", ohlcv)

    # identical input data each run -> the same candidate should win every time
    assert manager.stability_score("BTC/USDT", "trending_up") == 1.0
    assert manager.winner_history_for("BTC/USDT", "trending_up") == ["sma_crossover", "sma_crossover", "sma_crossover"]


def test_stability_score_reflects_a_flip_between_runs():
    manager = StrategyManager(train_window_bars=60, test_window_bars=20, min_folds_required=2)

    # directly seed winner_history to test the scoring math deterministically,
    # rather than depending on real backtests happening to flip
    manager.winner_history["BTC/USDT"] = {
        "trending_up": ["a", "a", "b", "b", "a"],
        "trending_down": [],
        "ranging": [],
    }

    # transitions: a->a (match), a->b (flip), b->b (match), b->a (flip) = 2/4 matches
    assert manager.stability_score("BTC/USDT", "trending_up") == 0.5


def test_winner_history_for_returns_empty_list_for_unknown_symbol():
    manager = StrategyManager()
    assert manager.winner_history_for("UNKNOWN/USDT", "trending_up") == []


def test_winner_history_is_bounded_over_a_long_run():
    """24/7 memory-leak regression guard: winner_history grows once per
    tournament run per symbol/regime - much slower than a per-cycle
    append, but still unbounded over months of repeated retraining. The
    stability score only ever looks at recent history, so trimming from
    the front (oldest first) costs nothing real."""
    manager = StrategyManager(candidates=[])

    for i in range(StrategyManager.MAX_WINNER_HISTORY_LENGTH + 50):
        result = TournamentResult(
            symbol="BTC/USDT",
            winners={
                regime: RegimeSelection(regime=regime, winner=f"candidate_{i}", reason="test")
                for regime in REGIMES
            },
        )
        manager._record_winner_history("BTC/USDT", result)

    history = manager.winner_history_for("BTC/USDT", "trending_up")
    assert len(history) == StrategyManager.MAX_WINNER_HISTORY_LENGTH
    assert history[-1] == f"candidate_{StrategyManager.MAX_WINNER_HISTORY_LENGTH + 49}"  # most recent kept
    assert history[0] == "candidate_50"  # oldest 50 trimmed from the front


def test_run_tournament_deploys_a_live_instance_per_candidate():
    manager = StrategyManager(train_window_bars=60, test_window_bars=20, min_folds_required=2)
    ohlcv = _uptrend_ohlcv(n=300)

    manager.run_tournament("BTC/USDT", ohlcv)

    deployed_names = set(manager.live_instances["BTC/USDT"].keys())
    assert deployed_names == {
        "ml_ensemble", "sma_crossover", "rsi_mean_reversion", "macd", "bollinger_bands", "candlestick_patterns",
        "time_series_momentum", "smart_money_concepts",
    }
    # a rule-based instance must be a real, fresh strategy object, not a placeholder
    assert hasattr(manager.live_instances["BTC/USDT"]["sma_crossover"], "generate_signal")


def test_live_signal_returns_hold_when_no_instance_deployed_yet():
    manager = StrategyManager()
    features = build_features(_uptrend_ohlcv(n=60))
    assert manager.live_signal("BTC/USDT", "sma_crossover", features) == Signal.HOLD


def test_live_signal_uses_deployed_rule_instance_and_accumulates_state():
    manager = StrategyManager(
        candidates=[StrategyCandidate("sma_crossover", "rule", SMACrossoverStrategy)],
        train_window_bars=60,
        test_window_bars=20,
        min_folds_required=2,
    )
    ohlcv = _uptrend_ohlcv(n=300)
    manager.run_tournament("BTC/USDT", ohlcv)

    deployed_instance = manager.live_instances["BTC/USDT"]["sma_crossover"]
    features = build_features(ohlcv)

    signal = manager.live_signal("BTC/USDT", "sma_crossover", features)

    # the SAME deployed instance object is used across calls (stateful,
    # accumulating last_signal etc.) - not a fresh one recreated each time
    assert manager.live_instances["BTC/USDT"]["sma_crossover"] is deployed_instance
    assert signal in (Signal.BUY, Signal.SELL, Signal.HOLD)


def test_live_signal_uses_deployed_ml_instance():
    manager = StrategyManager(train_window_bars=60, test_window_bars=20, min_folds_required=2)
    ohlcv = _uptrend_ohlcv(n=300)
    manager.run_tournament("BTC/USDT", ohlcv)

    features = build_features(ohlcv)
    signal = manager.live_signal("BTC/USDT", "ml_ensemble", features)

    assert signal in (Signal.BUY, Signal.SELL, Signal.HOLD)


def test_run_tournament_from_live_passes_through_the_injected_exchange_to_a_fallback_feed():
    """Regression: StrategyManager defaulted every on-demand LiveFeed to a
    bare, always-mainnet ccxt instance since no exchange was ever threaded
    through - found via an audit while execution correctly ran on testnet."""
    sentinel_exchange = object()
    captured = {}

    class RecordingLiveFeed:
        def __init__(self, symbol, timeframe, exchange=None):
            captured["exchange"] = exchange

        def fetch_ohlcv_history(self, total_bars):
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    import trading_assistant.agents.strategy_manager as strategy_manager_module

    original_live_feed = strategy_manager_module.LiveFeed
    strategy_manager_module.LiveFeed = RecordingLiveFeed
    try:
        manager = StrategyManager(exchange=sentinel_exchange)
        try:
            manager.run_tournament_from_live("BTC/USDT")
        except Exception:  # noqa: BLE001 - the empty fake feed can't satisfy the full
            pass  # walk-forward pipeline; only the exchange threaded into LiveFeed matters here
    finally:
        strategy_manager_module.LiveFeed = original_live_feed

    assert captured["exchange"] is sentinel_exchange
