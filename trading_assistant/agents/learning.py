"""LearningAgent: retrains a prediction model on fresh historical data,
validates it with walk-forward backtesting across multiple sequential time
windows, and only hot-swaps the live model if it's consistently good across
those windows and beats whatever is currently live. This is the system's
adaptation loop - it never touches the live model on a whim, and a single
lucky backtest window is never enough to earn promotion.

Walk-forward validation, not one train/test split: a strategy that looks
good on one split can just be curve-fit to that slice of history. Instead,
a fixed-size training window is rolled forward across all available
history; each fold trains a fresh model on that window and backtests it on
the immediately-following, non-overlapping period - strictly forward in
time, so no fold's test data is ever touched during that fold's training.
The promotion decision is then based on the *aggregate* across every fold
(median Sharpe, drawdown, consistency), not any single one.

No data leakage: every feature in agents.features is causal (rolling/ewm
windows only look backward), so features at any row are identical whether
computed on the whole series or a prefix ending at that row - precomputing
build_features() once over the full history and then slicing folds from it
introduces no leakage. The one place a training window's last label touches
its immediately-following bar is standard for next-bar-direction labels
(label(t) = 1 if close(t+1) > close(t)) - not leakage, since that label is
only ever *consumed* once during training, never used as a feature, and
never available to the model at its own live inference time either.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from trading_assistant.agents.alerting import AlertManager
from trading_assistant.agents.features import FEATURE_COLUMNS, build_features, label_next_bar_direction
from trading_assistant.agents.prediction import PredictionAgent
from trading_assistant.backtesting.framework import BacktestEngineV2, OrderDirection
from trading_assistant.market_data.feed import LiveFeed
from trading_assistant.ml.drift import compute_baseline_distribution, compute_feature_drift_from_baseline, summarize_drift
from trading_assistant.ml.predictor import EnsemblePredictor, RandomForestPredictor, XGBPredictor
from trading_assistant.ml.shap_importance import compute_ensemble_feature_importance
from trading_assistant.persistence.experiment_log import ExperimentLog   # <-- NEW
from trading_assistant.persistence.journal import Journal
from trading_assistant.risk.backtest_sizing import risk_based_quantity
from trading_assistant.validation.significance import (
    baseline_p_value as _baseline_p_value,
    beats_baseline_significantly,
    benjamini_hochberg,
    clears_threshold_significantly,
    threshold_p_value as _threshold_p_value,
)

logger = logging.getLogger(__name__)


def _default_model_factory() -> Any:
    return EnsemblePredictor(
        predictors=[
            RandomForestPredictor(n_trees=100, max_depth=6),
            XGBPredictor(n_estimators=100, max_depth=4),
        ]
    )


@dataclass
class WalkForwardFoldResult:
    fold_index: int
    train_rows: int
    test_rows: int
    metrics: dict


@dataclass
class WalkForwardSummary:
    folds: List[WalkForwardFoldResult] = field(default_factory=list)
    num_folds: int = 0
    median_sharpe: float = 0.0
    mean_sharpe: float = 0.0
    pct_folds_profitable: float = 0.0
    worst_fold_drawdown_pct: float = 0.0
    total_trades: int = 0
    mean_win_rate: float = 0.0

    def to_dict(self) -> dict:
        return {
            "num_folds": self.num_folds,
            "median_sharpe": self.median_sharpe,
            "mean_sharpe": self.mean_sharpe,
            "pct_folds_profitable": self.pct_folds_profitable,
            "worst_fold_drawdown_pct": self.worst_fold_drawdown_pct,
            "total_trades": self.total_trades,
            "mean_win_rate": self.mean_win_rate,
            "fold_sharpes": [f.metrics["sharpe_ratio"] for f in self.folds],
        }


@dataclass
class RetrainOutcome:
    promoted: bool
    reason: str
    candidate_metrics: dict
    baseline_metrics: Optional[dict] = None


@dataclass
class CandidateEvaluation:
    symbol: str
    featured: pd.DataFrame
    walk_forward: WalkForwardSummary
    fold_sharpes: List[float]
    baseline_metrics: Optional[dict]
    min_folds_ok: bool
    pct_profitable_ok: bool
    drawdown_ok: bool
    threshold_p_value: Optional[float]
    baseline_p_value: Optional[float]


class LearningAgent:
    def __init__(
        self,
        prediction_agent: PredictionAgent,
        *,
        min_sharpe_to_promote: float = 0.5,
        max_drawdown_to_promote: float = 0.25,
        train_window_bars: int = 500,
        test_window_bars: int = 100,
        target_folds: int = 8,
        min_folds_required: int = 3,
        min_pct_folds_profitable: float = 0.5,
        min_trades_per_fold: int = 0,
        journal: Optional[Journal] = None,
        model_factory: Callable[[], Any] = _default_model_factory,
        feeds: Optional[Dict[str, Any]] = None,
        exchange: Optional[Any] = None,
        alert_manager: Optional[AlertManager] = None,
        experiment_log: Optional[ExperimentLog] = None,   # <-- NEW
        min_live_trades_for_decay_check: Optional[int] = None,
        decay_win_rate_fraction: float = 0.5,
        decay_lookback_trades: int = 30,
        risk_per_trade: Optional[float] = None,
        position_stop_loss_pct: float = 0.01,
        max_position_size: float = 0.1,
        commission_rate: float = 0.001,
        slippage_rate: float = 0.0005,
        periods_per_year: float = 252,
    ):
        self.prediction_agent = prediction_agent
        self.risk_per_trade = risk_per_trade
        self.position_stop_loss_pct = position_stop_loss_pct
        self.max_position_size = max_position_size
        self.commission_rate = commission_rate
        self.slippage_rate = slippage_rate
        self.periods_per_year = periods_per_year
        self.min_sharpe_to_promote = min_sharpe_to_promote
        self.max_drawdown_to_promote = max_drawdown_to_promote
        self.train_window_bars = train_window_bars
        self.test_window_bars = test_window_bars
        self.target_folds = target_folds
        self.min_folds_required = min_folds_required
        self.min_pct_folds_profitable = min_pct_folds_profitable
        self.min_trades_per_fold = min_trades_per_fold
        self.journal = journal
        self.model_factory = model_factory
        self.min_live_trades_for_decay_check = min_live_trades_for_decay_check
        self.decay_win_rate_fraction = decay_win_rate_fraction
        self.decay_lookback_trades = decay_lookback_trades
        self._feeds: Dict[str, Any] = feeds or {}
        self._exchange = exchange
        self.alert_manager = alert_manager
        self.experiment_log = experiment_log   # <-- NEW
        self.last_promoted_metrics: Dict[str, dict] = {}

    @property
    def buy_threshold(self) -> float:
        return self.prediction_agent.buy_threshold

    @property
    def sell_threshold(self) -> float:
        return self.prediction_agent.sell_threshold

    def retrain_from_live(
        self, symbol: str, timeframe: str = "1h", total_bars: Optional[int] = None, feed: Any = None
    ) -> RetrainOutcome:
        ohlcv = self.fetch_ohlcv_for_retrain(symbol, timeframe, total_bars, feed)
        return self.retrain(symbol, ohlcv)

    def retrain_all_from_live(
        self, symbols: List[str], timeframe: str = "1h", total_bars: Optional[int] = None
    ) -> Dict[str, RetrainOutcome]:
        symbols_and_ohlcv = [
            (symbol, self.fetch_ohlcv_for_retrain(symbol, timeframe, total_bars, feed=None)) for symbol in symbols
        ]
        return self.retrain_all(symbols_and_ohlcv)

    def fetch_ohlcv_for_retrain(
        self, symbol: str, timeframe: str = "1h", total_bars: Optional[int] = None, feed: Any = None
    ) -> pd.DataFrame:
        feed = feed or self._feeds.get(symbol) or LiveFeed(symbol=symbol, timeframe=timeframe, exchange=self._exchange)
        if total_bars is None:
            total_bars = self.train_window_bars + self.target_folds * self.test_window_bars + 50
        if hasattr(feed, "fetch_ohlcv_history"):
            return feed.fetch_ohlcv_history(total_bars=total_bars)
        return feed.fetch_ohlcv(limit=total_bars)

    def retrain(self, symbol: str, ohlcv: pd.DataFrame) -> RetrainOutcome:
        # --- experiment log start (single symbol) ---
        experiment_id = None
        if self.experiment_log is not None:
            experiment_id = f"RET-{symbol}-{pd.Timestamp.now().strftime('%Y%m%d%H%M%S')}"
            self.experiment_log.start_experiment(
                experiment_id=experiment_id,
                goal=f"Walk-forward retrain for {symbol}",
                changes=(
                    f"train_window={self.train_window_bars}, test_window={self.test_window_bars}, "
                    f"target_folds={self.target_folds}, min_folds={self.min_folds_required}, "
                    f"min_sharpe={self.min_sharpe_to_promote}, min_pct_profitable={self.min_pct_folds_profitable}, "
                    f"buy_threshold={self.buy_threshold}, sell_threshold={self.sell_threshold}, "
                    f"risk_per_trade={self.risk_per_trade}, max_position_size={self.max_position_size}, "
                    f"commission_rate={self.commission_rate}, slippage_rate={self.slippage_rate}"
                ),
            )
        # --- end experiment log start ---

        evaluation = self._evaluate_candidate(symbol, ohlcv)
        threshold_significant = clears_threshold_significantly(evaluation.fold_sharpes, self.min_sharpe_to_promote)
        baseline_significant = self._uncorrected_baseline_significant(evaluation)
        outcome = self._finalize_candidate(evaluation, threshold_significant, baseline_significant)

        # --- experiment log conclude ---
        if self.experiment_log is not None and experiment_id is not None:
            metrics = outcome.candidate_metrics
            self.experiment_log.record_result(experiment_id, {
                'sharpe': metrics.get('median_sharpe', 0.0),
                'profit_factor': 0.0,   # placeholder – fold-level profit factor not yet captured here
                'net_pnl': 0.0,
                'win_rate': metrics.get('pct_folds_profitable', 0.0),
                'max_drawdown': metrics.get('worst_fold_drawdown_pct', 0.0),
            })
            decision = "promoted" if outcome.promoted else "rejected"
            self.experiment_log.conclude_experiment(experiment_id, decision)
        # --- end experiment log ---

        return outcome

    def retrain_all(self, symbols_and_ohlcv: List[Tuple[str, pd.DataFrame]]) -> Dict[str, RetrainOutcome]:
        evaluations = [self._evaluate_candidate(symbol, ohlcv) for symbol, ohlcv in symbols_and_ohlcv]

        threshold_corrected = benjamini_hochberg([e.threshold_p_value for e in evaluations])
        baseline_corrected = benjamini_hochberg([e.baseline_p_value for e in evaluations])

        outcomes: Dict[str, RetrainOutcome] = {}
        for i, evaluation in enumerate(evaluations):
            # --- experiment log start (per symbol in batch) ---
            experiment_id = None
            if self.experiment_log is not None:
                experiment_id = f"RET-{evaluation.symbol}-{pd.Timestamp.now().strftime('%Y%m%d%H%M%S')}-batch"
                self.experiment_log.start_experiment(
                    experiment_id=experiment_id,
                    goal=f"Batch walk-forward retrain for {evaluation.symbol}",
                    changes=(
                        f"train_window={self.train_window_bars}, test_window={self.test_window_bars}, "
                        f"target_folds={self.target_folds}, min_folds={self.min_folds_required}, "
                        f"min_sharpe={self.min_sharpe_to_promote}, min_pct_profitable={self.min_pct_folds_profitable}, "
                        f"buy_threshold={self.buy_threshold}, sell_threshold={self.sell_threshold}, "
                        f"risk_per_trade={self.risk_per_trade}, max_position_size={self.max_position_size}, "
                        f"commission_rate={self.commission_rate}, slippage_rate={self.slippage_rate}"
                    ),
                )
            # --- end experiment log start ---

            if evaluation.threshold_p_value is not None:
                threshold_significant = threshold_corrected[i]
            else:
                threshold_significant = clears_threshold_significantly(
                    evaluation.fold_sharpes, self.min_sharpe_to_promote
                )

            if evaluation.baseline_p_value is not None:
                baseline_significant = baseline_corrected[i]
            else:
                baseline_significant = self._uncorrected_baseline_significant(evaluation)

            outcome = self._finalize_candidate(
                evaluation, threshold_significant, baseline_significant
            )

            # --- experiment log conclude ---
            if self.experiment_log is not None and experiment_id is not None:
                metrics = outcome.candidate_metrics
                self.experiment_log.record_result(experiment_id, {
                    'sharpe': metrics.get('median_sharpe', 0.0),
                    'profit_factor': 0.0,
                    'net_pnl': 0.0,
                    'win_rate': metrics.get('pct_folds_profitable', 0.0),
                    'max_drawdown': metrics.get('worst_fold_drawdown_pct', 0.0),
                })
                decision = "promoted" if outcome.promoted else "rejected"
                self.experiment_log.conclude_experiment(experiment_id, decision)
            # --- end experiment log ---

            outcomes[evaluation.symbol] = outcome
        return outcomes

    def _uncorrected_baseline_significant(self, evaluation: "CandidateEvaluation") -> bool:
        baseline_metrics = evaluation.baseline_metrics
        if not baseline_metrics:
            return True
        baseline_fold_sharpes = baseline_metrics.get("fold_sharpes")
        if baseline_fold_sharpes:
            return beats_baseline_significantly(evaluation.fold_sharpes, baseline_fold_sharpes)
        return evaluation.walk_forward.median_sharpe > baseline_metrics.get("median_sharpe", float("-inf"))

    def _evaluate_candidate(self, symbol: str, ohlcv: pd.DataFrame) -> CandidateEvaluation:
        featured = build_features(ohlcv)
        featured["target"] = label_next_bar_direction(featured)
        featured = featured.dropna().reset_index(drop=True)

        walk_forward = self._walk_forward_validate(symbol, featured)
        baseline_metrics = self.last_promoted_metrics.get(symbol)
        fold_sharpes = [f.metrics["sharpe_ratio"] for f in walk_forward.folds]
        baseline_fold_sharpes = (baseline_metrics or {}).get("fold_sharpes")

        return CandidateEvaluation(
            symbol=symbol,
            featured=featured,
            walk_forward=walk_forward,
            fold_sharpes=fold_sharpes,
            baseline_metrics=baseline_metrics,
            min_folds_ok=walk_forward.num_folds >= self.min_folds_required,
            pct_profitable_ok=walk_forward.pct_folds_profitable >= self.min_pct_folds_profitable,
            drawdown_ok=abs(walk_forward.worst_fold_drawdown_pct) / 100 <= self.max_drawdown_to_promote,
            threshold_p_value=_threshold_p_value(fold_sharpes, self.min_sharpe_to_promote),
            baseline_p_value=(
                _baseline_p_value(fold_sharpes, baseline_fold_sharpes) if baseline_fold_sharpes else None
            ),
        )

    def _finalize_candidate(
        self, evaluation: CandidateEvaluation, threshold_significant: bool, baseline_significant: bool
    ) -> RetrainOutcome:
        symbol = evaluation.symbol
        walk_forward = evaluation.walk_forward
        baseline_metrics = evaluation.baseline_metrics
        featured = evaluation.featured

        if not evaluation.min_folds_ok:
            return self._finish(
                RetrainOutcome(
                    promoted=False,
                    reason=(
                        f"only {walk_forward.num_folds} walk-forward folds available "
                        f"(need {self.min_folds_required}) - not enough history yet"
                    ),
                    candidate_metrics=walk_forward.to_dict(),
                    baseline_metrics=baseline_metrics,
                ),
                symbol,
            )

        if not threshold_significant:
            return self._finish(
                RetrainOutcome(
                    promoted=False,
                    reason=(
                        f"median walk-forward sharpe {walk_forward.median_sharpe:.2f} does not "
                        f"SIGNIFICANTLY clear threshold {self.min_sharpe_to_promote} across folds "
                        f"(not just a point estimate - the fold-to-fold spread doesn't support it)"
                    ),
                    candidate_metrics=walk_forward.to_dict(),
                    baseline_metrics=baseline_metrics,
                ),
                symbol,
            )

        if not evaluation.pct_profitable_ok:
            return self._finish(
                RetrainOutcome(
                    promoted=False,
                    reason=(
                        f"only {walk_forward.pct_folds_profitable:.0%} of folds were profitable "
                        f"(need {self.min_pct_folds_profitable:.0%}) - too regime-dependent"
                    ),
                    candidate_metrics=walk_forward.to_dict(),
                    baseline_metrics=baseline_metrics,
                ),
                symbol,
            )

        if not evaluation.drawdown_ok:
            return self._finish(
                RetrainOutcome(
                    promoted=False,
                    reason=f"worst-fold drawdown {walk_forward.worst_fold_drawdown_pct:.1f}% exceeds threshold",
                    candidate_metrics=walk_forward.to_dict(),
                    baseline_metrics=baseline_metrics,
                ),
                symbol,
            )

        if baseline_metrics and not baseline_significant:
            return self._finish(
                RetrainOutcome(
                    promoted=False,
                    reason=(
                        "candidate did not SIGNIFICANTLY beat the currently live model's "
                        "walk-forward performance (not just a higher point-estimate median)"
                    ),
                    candidate_metrics=walk_forward.to_dict(),
                    baseline_metrics=baseline_metrics,
                ),
                symbol,
            )

        # Promote
        candidate = self.model_factory()
        candidate.train(featured[FEATURE_COLUMNS], featured["target"])
        self.prediction_agent.promote_model(symbol, candidate, FEATURE_COLUMNS)
        self.last_promoted_metrics[symbol] = walk_forward.to_dict()
        self._capture_feature_baseline(symbol, featured)
        self._capture_feature_importance(symbol, candidate, featured)

        return self._finish(
            RetrainOutcome(
                promoted=True,
                reason=f"walk-forward validated across {walk_forward.num_folds} folds, beat thresholds and prior model",
                candidate_metrics=walk_forward.to_dict(),
            ),
            symbol,
        )

    def _walk_forward_validate(self, symbol: str, featured: pd.DataFrame) -> WalkForwardSummary:
        folds: List[WalkForwardFoldResult] = []
        start = 0
        fold_index = 0

        while True:
            train_end = start + self.train_window_bars
            test_end = train_end + self.test_window_bars
            if test_end > len(featured):
                break

            train_slice = featured.iloc[start:train_end]
            test_slice = featured.iloc[train_end:test_end]

            model = self.model_factory()
            model.train(train_slice[FEATURE_COLUMNS], train_slice["target"])
            metrics = self._backtest(model, symbol, test_slice)

            folds.append(
                WalkForwardFoldResult(
                    fold_index=fold_index, train_rows=len(train_slice), test_rows=len(test_slice), metrics=metrics
                )
            )

            fold_index += 1
            start += self.test_window_bars

        if self.min_trades_per_fold > 0:
            folds = [f for f in folds if f.metrics["num_trades"] >= self.min_trades_per_fold]

        if not folds:
            return WalkForwardSummary()

        sharpes = [f.metrics["sharpe_ratio"] for f in folds]
        returns = [f.metrics["total_return_pct"] for f in folds]
        drawdowns = [f.metrics["max_drawdown_pct"] for f in folds]
        trades = [f.metrics["num_trades"] for f in folds]
        total_trades = int(sum(trades))
        mean_win_rate = (
            sum(f.metrics["win_rate"] * f.metrics["num_trades"] for f in folds) / total_trades
            if total_trades > 0 else 0.0
        )

        return WalkForwardSummary(
            folds=folds,
            num_folds=len(folds),
            median_sharpe=float(np.median(sharpes)),
            mean_sharpe=float(np.mean(sharpes)),
            pct_folds_profitable=sum(1 for r in returns if r > 0) / len(folds),
            worst_fold_drawdown_pct=float(min(drawdowns)),
            total_trades=total_trades,
            mean_win_rate=float(mean_win_rate),
        )

    def _backtest(self, model: Any, symbol: str, test_df: pd.DataFrame) -> dict:
        engine = BacktestEngineV2(
            initial_capital=10000.0, commission=self.commission_rate, slippage=self.slippage_rate,
            periods_per_year=self.periods_per_year,
        )
        predictions = np.clip(model.predict(test_df[FEATURE_COLUMNS]), 0.0, 1.0)
        position = None

        rows = list(test_df.itertuples(index=False))
        for probability_up, row in zip(predictions, rows):
            price = float(row.close)
            timestamp = getattr(row, "timestamp", None)

            if price <= 0:
                continue

            if self.risk_per_trade is not None:
                quantity = risk_based_quantity(
                    engine.capital, price, self.position_stop_loss_pct,
                    risk_per_trade=self.risk_per_trade, max_position_size=self.max_position_size,
                )
            else:
                quantity = (engine.capital * 0.95) / price

            if probability_up >= self.buy_threshold and position is None and quantity > 0:
                if engine.open_position(symbol, OrderDirection.LONG, price, timestamp, quantity=quantity):
                    position = engine.positions[-1]
            elif probability_up <= self.sell_threshold and position is not None:
                engine.close_position(position, price, timestamp)
                position = None

            engine.update_equity({symbol: price})

        if position is not None:
            last_row = rows[-1]
            engine.close_position(position, float(last_row.close), getattr(last_row, "timestamp", None))

        result = engine.get_result()
        return {
            "sharpe_ratio": result.sharpe_ratio,
            "max_drawdown_pct": result.max_drawdown_pct,
            "win_rate": result.win_rate,
            "num_trades": result.num_trades,
            "total_return_pct": result.total_return_pct,
        }

    def _capture_feature_baseline(self, symbol: str, featured: pd.DataFrame) -> None:
        if self.journal is None:
            return
        distributions = {
            col: compute_baseline_distribution(featured[col])
            for col in FEATURE_COLUMNS
            if col in featured.columns
        }
        distributions = {col: dist for col, dist in distributions.items() if dist}
        if distributions:
            self.journal.record_feature_baseline(symbol=symbol, boundaries=distributions)

    def _capture_feature_importance(self, symbol: str, candidate: Any, featured: pd.DataFrame) -> None:
        if self.journal is None:
            return
        importance = compute_ensemble_feature_importance(candidate, featured[FEATURE_COLUMNS], FEATURE_COLUMNS)
        if importance:
            self.journal.record_feature_importance(symbol=symbol, importance=importance)

    def check_feature_drift(self, symbol: str, live_features: pd.DataFrame) -> Optional[dict]:
        if self.journal is None:
            return None
        baseline = self.journal.latest_feature_baseline(symbol)
        if baseline is None:
            return None

        feature_psi = compute_feature_drift_from_baseline(baseline["boundaries"], live_features, FEATURE_COLUMNS)
        summary = summarize_drift(feature_psi)
        summary["symbol"] = symbol
        summary["baseline_captured_at"] = baseline["timestamp"]

        self.journal.record_drift_event(symbol=symbol, overall_severity=summary["overall_severity"], summary=summary)

        if summary["overall_severity"] in ("moderate", "significant"):
            logger.warning(
                "live feature distribution has drifted from the promoted model's training baseline",
                extra={"context": summary},
            )
            if self.alert_manager is not None:
                worst = summary["worst_features"][0] if summary["worst_features"] else None
                worst_desc = f"{worst['feature']} (PSI={worst['psi']})" if worst else "unknown feature"
                # Drift is an early-warning diagnostic, not an execution
                # emergency. The measured severity remains in `summary`, while
                # operator notification urgency is WARNING.
                severity = "warning"
                self.alert_manager.raise_alert(
                    "feature_drift",
                    severity,
                    f"{symbol}: live feature distribution drifted from training baseline, worst feature {worst_desc}",
                    context=summary,
                    dedupe_key=f"feature_drift:{symbol}",
                )

        return summary

    def check_live_decay(self, symbol: str, recent_trade_pnls: List[float]) -> Optional[dict]:
        baseline = self.last_promoted_metrics.get(symbol)
        if baseline is None or self.min_live_trades_for_decay_check is None:
            return None
        if len(recent_trade_pnls) < self.min_live_trades_for_decay_check:
            return None

        live_win_rate = sum(1 for p in recent_trade_pnls if p > 0) / len(recent_trade_pnls)
        baseline_win_rate = baseline.get("mean_win_rate", baseline.get("pct_folds_profitable", 0.0))
        net_pnl = sum(recent_trade_pnls)
        decayed = live_win_rate < baseline_win_rate * self.decay_win_rate_fraction and net_pnl < 0

        summary = {
            "symbol": symbol,
            "live_win_rate": live_win_rate,
            "baseline_win_rate": baseline_win_rate,
            "net_pnl": net_pnl,
            "num_trades": len(recent_trade_pnls),
            "decayed": decayed,
        }

        if decayed:
            rolled_back = self.prediction_agent.rollback_model(symbol)
            summary["rolled_back"] = rolled_back
            logger.warning("live performance decay detected", extra={"context": summary})
            if self.journal is not None:
                self.journal.record_retrain_event(
                    promoted=False, symbol=symbol, reason="live performance decay - automatic rollback", metrics=summary
                )
            if self.alert_manager is not None:
                self.alert_manager.raise_alert(
                    "model_decay",
                    "critical",
                    (
                        f"{symbol}: live win rate ({live_win_rate:.0%}) has decayed well below its validated "
                        f"walk-forward rate ({baseline_win_rate:.0%}) and recent trades are net-losing"
                        + (" - rolled back to the previous model" if rolled_back else " - no previous model to roll back to")
                    ),
                    context=summary,
                    dedupe_key=f"model_decay:{symbol}",
                )

        return summary

    def _finish(self, outcome: RetrainOutcome, symbol: str) -> RetrainOutcome:
        if self.journal is not None:
            self.journal.record_retrain_event(
                promoted=outcome.promoted,
                symbol=symbol,
                reason=outcome.reason,
                metrics={"candidate": outcome.candidate_metrics, "baseline": outcome.baseline_metrics},
            )
        logger.info(
            "retrain attempt",
            extra={
                "context": {
                    "symbol": symbol,
                    "promoted": outcome.promoted,
                    "reason": outcome.reason,
                    "metrics": outcome.candidate_metrics,
                }
            },
        )
        return outcome