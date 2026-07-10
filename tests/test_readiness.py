from datetime import datetime, timedelta, timezone

from trading_assistant.config import ReadinessConfig
from trading_assistant.readiness import (
    assess_backtest_live_correlation,
    assess_circuit_breaker,
    assess_elapsed_time,
    assess_real_trading_performance,
    assess_survivorship_bias_warnings,
    assess_tournament_stability,
    build_readiness_report,
)
from trading_assistant.validation.backtest_correlation import BacktestVsLiveRecord, SurvivorshipBiasRecord

CONFIG = ReadinessConfig(
    min_tournament_runs=5,
    min_stability_score=0.8,
    min_real_trades=3,
    min_real_sharpe=0.5,
    min_elapsed_days=2.0,
    min_records_for_correlation=3,
    min_live_trades_for_correlation=5,
)


def _positively_correlated_records(n=4):
    return [
        BacktestVsLiveRecord(
            symbol=f"SYM{i}/USDT", candidate="macd", backtest_sharpe=float(i), live_sharpe=float(i), live_trades=10
        )
        for i in range(n)
    ]


def test_tournament_stability_fails_when_no_symbol_reaches_run_threshold():
    histories = {"BTC/USDT": {"trending_up": ["macd", "macd"]}}  # only 2 runs, need 5

    result = assess_tournament_stability(histories, CONFIG)

    assert result.passed is False
    assert "reached" in result.detail


def test_tournament_stability_ignores_regimes_below_run_threshold():
    """A rarely-occurring regime with few runs must not silently pass or
    fail the criterion - it should simply not count until it has enough data."""
    histories = {
        "BTC/USDT": {
            "trending_up": ["macd"] * 5,  # stable, meets threshold
            "ranging": ["macd", "sma_crossover"],  # below threshold, must be ignored
        }
    }

    result = assess_tournament_stability(histories, CONFIG)

    assert result.passed is True


def test_tournament_stability_fails_when_a_qualifying_combo_is_unstable():
    histories = {
        "BTC/USDT": {"trending_up": ["macd"] * 5},
        "ETH/USDT": {"trending_up": ["macd", "sma_crossover", "macd", "sma_crossover", "macd"]},  # flip-flopping
    }

    result = assess_tournament_stability(histories, CONFIG)

    assert result.passed is False
    assert "ETH/USDT" in result.detail


def test_real_trading_performance_fails_with_too_few_trades():
    result = assess_real_trading_performance([10000.0, 10100.0], [50.0], CONFIG)

    assert result.passed is False
    assert "Only 1 real trade" in result.detail


def test_real_trading_performance_fails_with_low_sharpe():
    # enough trades, but a declining/flat equity curve -> low/negative sharpe
    equity_curve = [10000.0, 9990.0, 9980.0, 9970.0, 9960.0]
    result = assess_real_trading_performance(equity_curve, [-10.0, -10.0, -10.0], CONFIG)

    assert result.passed is False


def test_real_trading_performance_passes_with_enough_trades_and_good_sharpe():
    equity_curve = [10000.0 + i * 50 for i in range(30)]  # steady uptrend -> high sharpe
    result = assess_real_trading_performance(equity_curve, [50.0, 60.0, 55.0], CONFIG)

    assert result.passed is True


def test_elapsed_time_fails_when_too_recent():
    now = datetime.now(timezone.utc)
    first_timestamp = (now - timedelta(days=1)).isoformat()  # only 1 day, need 2

    result = assess_elapsed_time(first_timestamp, CONFIG, now=now)

    assert result.passed is False


def test_elapsed_time_passes_after_enough_days():
    now = datetime.now(timezone.utc)
    first_timestamp = (now - timedelta(days=3)).isoformat()

    result = assess_elapsed_time(first_timestamp, CONFIG, now=now)

    assert result.passed is True


def test_elapsed_time_fails_with_no_history():
    result = assess_elapsed_time(None, CONFIG)

    assert result.passed is False


def test_circuit_breaker_fails_when_tripped():
    result = assess_circuit_breaker({"tripped": True, "reason": "daily loss limit"})

    assert result.passed is False


def test_circuit_breaker_passes_when_never_tripped():
    assert assess_circuit_breaker(None).passed is True
    assert assess_circuit_breaker({"tripped": False}).passed is True


def test_build_readiness_report_not_ready_when_any_criterion_fails(tmp_path):
    now = datetime.now(timezone.utc)
    report = build_readiness_report(
        winner_histories={},
        real_equity_curve=[10000.0],
        real_trade_pnls=[],
        first_timestamp=(now - timedelta(days=10)).isoformat(),  # this one passes
        circuit_breaker_event=None,
        config=CONFIG,
        now=now,
    )

    assert report.overall_ready is False  # tournament + real trading both fail
    assert len(report.criteria) == 5


def test_build_readiness_report_ready_when_everything_passes():
    now = datetime.now(timezone.utc)
    histories = {"BTC/USDT": {"trending_up": ["macd"] * 5}}
    equity_curve = [10000.0 + i * 50 for i in range(30)]

    report = build_readiness_report(
        winner_histories=histories,
        real_equity_curve=equity_curve,
        real_trade_pnls=[50.0, 60.0, 55.0],
        first_timestamp=(now - timedelta(days=10)).isoformat(),
        circuit_breaker_event={"tripped": False},
        config=CONFIG,
        backtest_live_records=_positively_correlated_records(),
        now=now,
    )

    assert report.overall_ready is True
    assert all(c.passed for c in report.criteria)


def test_backtest_live_correlation_fails_without_enough_qualifying_records():
    result = assess_backtest_live_correlation(_positively_correlated_records(n=1), CONFIG)

    assert result.passed is False
    assert "Only 1" in result.detail


def test_backtest_live_correlation_passes_on_positive_correlation():
    result = assess_backtest_live_correlation(_positively_correlated_records(), CONFIG)

    assert result.passed is True
    assert "positive" in result.detail


def test_backtest_live_correlation_fails_on_negative_correlation():
    records = [
        BacktestVsLiveRecord(
            symbol=f"SYM{i}/USDT", candidate="macd", backtest_sharpe=float(i), live_sharpe=-float(i), live_trades=10
        )
        for i in range(4)
    ]

    result = assess_backtest_live_correlation(records, CONFIG)

    assert result.passed is False
    assert "negative" in result.detail


def test_backtest_live_correlation_passes_when_weak_or_inconclusive():
    """Early on, a weak/near-zero correlation with few data points is
    expected and must not itself block readiness - only a clear negative
    correlation is disqualifying."""
    records = [
        BacktestVsLiveRecord(symbol="A/USDT", candidate="macd", backtest_sharpe=1.0, live_sharpe=1.05, live_trades=10),
        BacktestVsLiveRecord(symbol="B/USDT", candidate="macd", backtest_sharpe=1.0, live_sharpe=0.9, live_trades=10),
        BacktestVsLiveRecord(symbol="C/USDT", candidate="macd", backtest_sharpe=1.1, live_sharpe=1.0, live_trades=10),
    ]

    result = assess_backtest_live_correlation(records, CONFIG)

    assert result.passed is True


def test_survivorship_bias_warnings_flags_champion_below_baseline():
    records = [
        SurvivorshipBiasRecord(
            symbol="BTC/USDT", regime="trending_up", champion="macd",
            champion_live_sharpe=0.3, champion_live_trades=10,
            avg_candidate_backtest_sharpe=1.5, beats_baseline=False,
        )
    ]

    warnings = assess_survivorship_bias_warnings(records)

    assert len(warnings) == 1
    assert "BTC/USDT" in warnings[0] and "macd" in warnings[0]


def test_survivorship_bias_warnings_silent_when_champion_beats_baseline():
    records = [
        SurvivorshipBiasRecord(
            symbol="BTC/USDT", regime="trending_up", champion="macd",
            champion_live_sharpe=2.0, champion_live_trades=10,
            avg_candidate_backtest_sharpe=1.5, beats_baseline=True,
        )
    ]

    assert assess_survivorship_bias_warnings(records) == []


def test_survivorship_bias_warnings_never_affect_overall_ready():
    """The core guarantee: this is a diagnostic, not a gate - a report with
    warnings but otherwise-passing criteria must still be overall_ready."""
    underperforming = [
        SurvivorshipBiasRecord(
            symbol="BTC/USDT", regime="trending_up", champion="macd",
            champion_live_sharpe=0.1, champion_live_trades=10,
            avg_candidate_backtest_sharpe=2.0, beats_baseline=False,
        )
    ]
    now = datetime.now(timezone.utc)
    report = build_readiness_report(
        winner_histories={"BTC/USDT": {"trending_up": ["macd"] * 5}},
        real_equity_curve=[10000.0] * 20,
        real_trade_pnls=[10.0] * 5,
        first_timestamp=(now - timedelta(days=10)).isoformat(),
        circuit_breaker_event=None,
        config=ReadinessConfig(
            min_tournament_runs=5, min_stability_score=0.5, min_real_trades=3, min_real_sharpe=-100.0,
            min_elapsed_days=2.0, min_records_for_correlation=0, min_live_trades_for_correlation=5,
        ),
        survivorship_records=underperforming,
        now=now,
    )

    assert len(report.warnings) == 1
    assert all(c.passed for c in report.criteria)  # the warning didn't fail any criterion
    assert report.overall_ready is True
