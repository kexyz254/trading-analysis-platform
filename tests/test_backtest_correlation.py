import pytest

from trading_assistant.validation.backtest_correlation import (
    BacktestVsLiveRecord,
    SurvivorshipBiasRecord,
    average_backtest_sharpe,
    backtest_sharpe_by_candidate,
    build_records,
    build_survivorship_records,
    compute_correlation,
    correlation_verdict,
)


def _report(candidates):
    """candidates: {name: {regime: (median_sharpe, num_folds)}}"""
    return {
        "candidates": [
            {
                "name": name,
                "by_regime": {
                    regime: {"median_sharpe": sharpe, "num_folds": folds}
                    for regime, (sharpe, folds) in regimes.items()
                },
            }
            for name, regimes in candidates.items()
        ]
    }


class TestBacktestSharpeByCandidate:
    def test_weighted_average_across_regimes_with_folds(self):
        report = _report({"macd": {"trending_up": (1.0, 3), "ranging": (0.0, 1)}})
        result = backtest_sharpe_by_candidate(report)
        # (1.0*3 + 0.0*1) / 4 = 0.75
        assert result["macd"] == 0.75

    def test_regimes_with_zero_folds_excluded_from_weighting(self):
        report = _report({"macd": {"trending_up": (2.0, 5), "ranging": (-99.0, 0)}})
        result = backtest_sharpe_by_candidate(report)
        assert result["macd"] == 2.0

    def test_candidate_with_no_folds_anywhere_is_omitted(self):
        report = _report({"macd": {"trending_up": (0.0, 0)}})
        result = backtest_sharpe_by_candidate(report)
        assert "macd" not in result

    def test_multiple_candidates(self):
        report = _report({
            "macd": {"trending_up": (1.5, 4)},
            "sma_crossover": {"ranging": (0.2, 2)},
        })
        result = backtest_sharpe_by_candidate(report)
        assert result == {"macd": 1.5, "sma_crossover": 0.2}


class TestBuildRecords:
    def test_pairs_backtest_and_live_by_symbol_and_candidate(self):
        tournament_reports = {"BTC/USDT": _report({"macd": {"trending_up": (1.0, 5)}})}
        live_performance = {"BTC/USDT": {"macd": {"sharpe_ratio": 0.8, "num_trades": 10}}}

        records = build_records(tournament_reports, live_performance, min_live_trades=5)

        assert len(records) == 1
        assert records[0] == BacktestVsLiveRecord(
            symbol="BTC/USDT", candidate="macd", backtest_sharpe=1.0, live_sharpe=0.8, live_trades=10
        )

    def test_excludes_candidates_below_min_live_trades(self):
        tournament_reports = {"BTC/USDT": _report({"macd": {"trending_up": (1.0, 5)}})}
        live_performance = {"BTC/USDT": {"macd": {"sharpe_ratio": 0.8, "num_trades": 2}}}

        records = build_records(tournament_reports, live_performance, min_live_trades=5)

        assert records == []

    def test_excludes_candidates_with_no_live_data_at_all(self):
        tournament_reports = {"BTC/USDT": _report({"macd": {"trending_up": (1.0, 5)}})}
        live_performance = {}

        records = build_records(tournament_reports, live_performance, min_live_trades=5)

        assert records == []

    def test_multiple_symbols_and_candidates(self):
        tournament_reports = {
            "BTC/USDT": _report({"macd": {"trending_up": (1.0, 5)}}),
            "ETH/USDT": _report({"sma_crossover": {"ranging": (0.5, 5)}}),
        }
        live_performance = {
            "BTC/USDT": {"macd": {"sharpe_ratio": 0.9, "num_trades": 8}},
            "ETH/USDT": {"sma_crossover": {"sharpe_ratio": 0.3, "num_trades": 6}},
        }

        records = build_records(tournament_reports, live_performance, min_live_trades=5)

        assert len(records) == 2


class TestComputeCorrelation:
    def _records(self, backtest_live_pairs):
        return [
            BacktestVsLiveRecord(symbol=f"S{i}/USDT", candidate="c", backtest_sharpe=b, live_sharpe=l, live_trades=10)
            for i, (b, l) in enumerate(backtest_live_pairs)
        ]

    def test_insufficient_records_returns_none(self):
        records = self._records([(1.0, 1.0), (2.0, 2.0)])
        assert compute_correlation(records) is None

    def test_perfectly_positively_correlated(self):
        records = self._records([(1.0, 1.0), (2.0, 2.0), (3.0, 3.0), (4.0, 4.0)])
        correlation = compute_correlation(records)
        assert correlation is not None
        assert correlation == 1.0

    def test_perfectly_negatively_correlated(self):
        records = self._records([(1.0, 4.0), (2.0, 3.0), (3.0, 2.0), (4.0, 1.0)])
        correlation = compute_correlation(records)
        assert correlation is not None
        assert correlation == -1.0

    def test_zero_variance_side_returns_none(self):
        records = self._records([(1.0, 5.0), (1.0, 3.0), (1.0, 8.0)])  # backtest_sharpe constant
        assert compute_correlation(records) is None


class TestCorrelationVerdict:
    def test_none_is_insufficient_data(self):
        assert correlation_verdict(None) == "insufficient_data"

    def test_strong_positive(self):
        assert correlation_verdict(0.5) == "positive"
        assert correlation_verdict(0.2) == "positive"

    def test_weak_or_none(self):
        assert correlation_verdict(0.1) == "weak_or_none"
        assert correlation_verdict(-0.1) == "weak_or_none"
        assert correlation_verdict(0.0) == "weak_or_none"

    def test_negative_red_flag(self):
        assert correlation_verdict(-0.2) == "negative"
        assert correlation_verdict(-0.9) == "negative"


class TestAverageBacktestSharpe:
    def test_averages_across_every_candidate_not_just_the_winner(self):
        report = _report({
            "macd": {"trending_up": (2.0, 5)},        # the eventual champion, highest Sharpe
            "sma_crossover": {"trending_up": (0.5, 5)},
            "rsi_mean_reversion": {"trending_up": (0.1, 5)},
        })
        # (2.0 + 0.5 + 0.1) / 3, NOT just the champion's 2.0
        assert average_backtest_sharpe(report) == pytest.approx(0.8666666, abs=1e-5)

    def test_none_when_no_candidate_has_any_folds(self):
        report = _report({"macd": {"trending_up": (0.0, 0)}})
        assert average_backtest_sharpe(report) is None


class TestBuildSurvivorshipRecords:
    def test_flags_a_champion_underperforming_the_unbiased_baseline(self):
        tournament_reports = {
            "BTC/USDT": _report({
                "macd": {"trending_up": (2.0, 5)},
                "sma_crossover": {"trending_up": (0.5, 5)},
            })  # unbiased average = 1.25
        }
        winners = {"BTC/USDT": {"trending_up": "macd"}}
        live_performance = {"BTC/USDT": {"macd": {"sharpe_ratio": 0.3, "num_trades": 10}}}

        records = build_survivorship_records(tournament_reports, winners, live_performance, min_live_trades=5)

        assert len(records) == 1
        record = records[0]
        assert record == SurvivorshipBiasRecord(
            symbol="BTC/USDT", regime="trending_up", champion="macd",
            champion_live_sharpe=0.3, champion_live_trades=10,
            avg_candidate_backtest_sharpe=1.25, beats_baseline=False,
        )

    def test_champion_beating_the_baseline_is_not_flagged_as_underperforming(self):
        tournament_reports = {
            "BTC/USDT": _report({
                "macd": {"trending_up": (2.0, 5)},
                "sma_crossover": {"trending_up": (0.5, 5)},
            })
        }
        winners = {"BTC/USDT": {"trending_up": "macd"}}
        live_performance = {"BTC/USDT": {"macd": {"sharpe_ratio": 3.0, "num_trades": 10}}}

        records = build_survivorship_records(tournament_reports, winners, live_performance, min_live_trades=5)

        assert records[0].beats_baseline is True

    def test_no_champion_for_a_regime_produces_no_record(self):
        tournament_reports = {"BTC/USDT": _report({"macd": {"trending_up": (2.0, 5)}})}
        winners = {"BTC/USDT": {"trending_up": None}}
        live_performance = {"BTC/USDT": {"macd": {"sharpe_ratio": 3.0, "num_trades": 10}}}

        records = build_survivorship_records(tournament_reports, winners, live_performance, min_live_trades=5)

        assert records == []

    def test_champion_with_too_few_live_trades_is_excluded(self):
        tournament_reports = {"BTC/USDT": _report({"macd": {"trending_up": (2.0, 5)}})}
        winners = {"BTC/USDT": {"trending_up": "macd"}}
        live_performance = {"BTC/USDT": {"macd": {"sharpe_ratio": 3.0, "num_trades": 2}}}

        records = build_survivorship_records(tournament_reports, winners, live_performance, min_live_trades=5)

        assert records == []
