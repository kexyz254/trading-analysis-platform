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
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from trading_assistant.agents.alerting import AlertManager
from trading_assistant.agents.features import FEATURE_COLUMNS, build_features, label_next_bar_direction
from trading_assistant.agents.prediction import PredictionAgent
from trading_assistant.backtesting.framework import BacktestEngineV2, OrderDirection
from trading_assistant.market_data.feed import LiveFeed
from trading_assistant.ml.drift import compute_baseline_distribution, compute_feature_drift_from_baseline, summarize_drift
from trading_assistant.ml.predictor import EnsemblePredictor, RandomForestPredictor, XGBPredictor
from trading_assistant.persistence.journal import Journal
from trading_assistant.risk.backtest_sizing import risk_based_quantity
from trading_assistant.validation.significance import beats_baseline_significantly, clears_threshold_significantly

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
    # Trade-weighted average of each fold's own per-trade win_rate - NOT
    # the same thing as pct_folds_profitable (the fraction of FOLD-level
    # windows that ended net-positive, a much coarser signal). Distinct on
    # purpose: check_live_decay compares live per-trade win rate against
    # this, and comparing it against pct_folds_profitable instead used to
    # compare two different metrics as if they were the same one (a model
    # can clear 90% of fold windows net-positive while its actual per-trade
    # win rate is ~35% on a few big winners) - a real decay could be masked,
    # or a healthy live run could trip a false rollback.
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
            # Raw per-fold Sharpe values, not just the summary median - the
            # statistical significance checks (trading_assistant.validation.
            # significance) need the actual spread across folds, not a
            # single point estimate.
            "fold_sharpes": [f.metrics["sharpe_ratio"] for f in self.folds],
        }


@dataclass
class RetrainOutcome:
    promoted: bool
    reason: str
    candidate_metrics: dict
    baseline_metrics: Optional[dict] = None


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
        # See LearningConfig.min_folds_required's docstring (trading_assistant/
        # config.py) for why 3, not 5 - matches the live system's actual
        # config.yaml default now.
        min_folds_required: int = 3,
        min_pct_folds_profitable: float = 0.5,
        journal: Optional[Journal] = None,
        model_factory: Callable[[], Any] = _default_model_factory,
        feeds: Optional[Dict[str, Any]] = None,
        exchange: Optional[Any] = None,
        alert_manager: Optional[AlertManager] = None,
        # Live-performance-triggered rollback: once at least
        # min_live_trades_for_decay_check real trades have closed for a
        # symbol since its model was promoted, compare their actual win
        # rate against what walk-forward validated. None disables it
        # entirely (opt-in, backward compatible).
        min_live_trades_for_decay_check: Optional[int] = None,
        decay_win_rate_fraction: float = 0.5,
        decay_lookback_trades: int = 30,
        # Walk-forward sizing parity (trading_assistant.risk.backtest_sizing) -
        # see StrategyManager's identical parameter for why: a flat ~95%-
        # of-capital backtest sizing describes performance at a risk level
        # the model would never actually be traded at live. None keeps the
        # old flat sizing (opt-in, backward compatible).
        risk_per_trade: Optional[float] = None,
        position_stop_loss_pct: float = 0.01,
        max_position_size: float = 0.1,
        # Same real-world friction assumption PortfolioConfig's
        # commission_rate/slippage_rate feeds the LIVE portfolio -
        # BacktestEngineV2's own hardcoded defaults (0.001/0.0005) happen
        # to match those defaults today, but nothing previously connected
        # the two: an operator changing config.yaml's real fee assumption
        # (VIP tier, BNB discount, exchange change) would silently leave
        # every walk-forward promotion decision validating candidates
        # against a stale friction assumption forever after.
        commission_rate: float = 0.001,
        slippage_rate: float = 0.0005,
    ):
        self.prediction_agent = prediction_agent
        self.risk_per_trade = risk_per_trade
        self.position_stop_loss_pct = position_stop_loss_pct
        self.max_position_size = max_position_size
        self.commission_rate = commission_rate
        self.slippage_rate = slippage_rate
        self.min_sharpe_to_promote = min_sharpe_to_promote
        self.max_drawdown_to_promote = max_drawdown_to_promote
        self.train_window_bars = train_window_bars
        self.test_window_bars = test_window_bars
        self.target_folds = target_folds
        self.min_folds_required = min_folds_required
        self.min_pct_folds_profitable = min_pct_folds_profitable
        self.journal = journal
        self.model_factory = model_factory
        self.min_live_trades_for_decay_check = min_live_trades_for_decay_check
        self.decay_win_rate_fraction = decay_win_rate_fraction
        self.decay_lookback_trades = decay_lookback_trades
        self._feeds: Dict[str, Any] = feeds or {}
        # Passed to any LiveFeed this agent constructs on demand (see
        # retrain_from_live) so walk-forward training data comes from the
        # SAME exchange (and testnet/mainnet mode) real execution uses -
        # without this, a bare LiveFeed() defaults to an unsandboxed,
        # always-mainnet ccxt instance regardless of config.mode.
        self._exchange = exchange
        self.alert_manager = alert_manager
        # Keyed by symbol - a per-symbol baseline, not a single shared one.
        # Otherwise BTC/USDT's promoted score would gate ETH/USDT's promotion
        # decisions (and vice versa), which makes no sense across symbols.
        self.last_promoted_metrics: Dict[str, dict] = {}

    @property
    def buy_threshold(self) -> float:
        # Read from prediction_agent rather than keeping an independent copy -
        # otherwise tuning live trading thresholds without also touching this
        # class would silently validate candidates against a stale rule that
        # isn't the one actually deployed.
        return self.prediction_agent.buy_threshold

    @property
    def sell_threshold(self) -> float:
        return self.prediction_agent.sell_threshold

    def retrain_from_live(
        self, symbol: str, timeframe: str = "1h", total_bars: Optional[int] = None, feed: Any = None
    ) -> RetrainOutcome:
        feed = feed or self._feeds.get(symbol) or LiveFeed(symbol=symbol, timeframe=timeframe, exchange=self._exchange)
        if total_bars is None:
            # Enough for target_folds walk-forward folds plus indicator warmup.
            total_bars = self.train_window_bars + self.target_folds * self.test_window_bars + 50
        if hasattr(feed, "fetch_ohlcv_history"):
            ohlcv = feed.fetch_ohlcv_history(total_bars=total_bars)
        else:
            ohlcv = feed.fetch_ohlcv(limit=total_bars)
        return self.retrain(symbol, ohlcv)

    def retrain(self, symbol: str, ohlcv: pd.DataFrame) -> RetrainOutcome:
        featured = build_features(ohlcv)
        featured["target"] = label_next_bar_direction(featured)
        featured = featured.dropna().reset_index(drop=True)

        walk_forward = self._walk_forward_validate(symbol, featured)
        baseline_metrics = self.last_promoted_metrics.get(symbol)

        if walk_forward.num_folds < self.min_folds_required:
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

        fold_sharpes = [f.metrics["sharpe_ratio"] for f in walk_forward.folds]
        if not clears_threshold_significantly(fold_sharpes, self.min_sharpe_to_promote):
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

        if walk_forward.pct_folds_profitable < self.min_pct_folds_profitable:
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

        if abs(walk_forward.worst_fold_drawdown_pct) / 100 > self.max_drawdown_to_promote:
            return self._finish(
                RetrainOutcome(
                    promoted=False,
                    reason=f"worst-fold drawdown {walk_forward.worst_fold_drawdown_pct:.1f}% exceeds threshold",
                    candidate_metrics=walk_forward.to_dict(),
                    baseline_metrics=baseline_metrics,
                ),
                symbol,
            )

        if baseline_metrics:
            baseline_fold_sharpes = baseline_metrics.get("fold_sharpes")
            if baseline_fold_sharpes:
                beats_baseline = beats_baseline_significantly(fold_sharpes, baseline_fold_sharpes)
            else:
                # A baseline recorded before fold-level history was tracked -
                # fall back to the old point-estimate comparison rather than
                # refusing to ever promote again just because of a missing key.
                beats_baseline = walk_forward.median_sharpe > baseline_metrics.get("median_sharpe", float("-inf"))
            if not beats_baseline:
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

        # Promote: train the model actually deployed live on ALL available
        # data (every fold's train+test rows), not just the last fold's
        # training window - walk-forward already proved this feature set/
        # model class holds up across many windows, so the final model gets
        # to see everything.
        candidate = self.model_factory()
        candidate.train(featured[FEATURE_COLUMNS], featured["target"])
        self.prediction_agent.promote_model(symbol, candidate, FEATURE_COLUMNS)
        self.last_promoted_metrics[symbol] = walk_forward.to_dict()
        self._capture_feature_baseline(symbol, featured)

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
            start += self.test_window_bars  # non-overlapping test windows; rolling (not expanding) train window

        if not folds:
            return WalkForwardSummary()

        sharpes = [f.metrics["sharpe_ratio"] for f in folds]
        returns = [f.metrics["total_return_pct"] for f in folds]
        drawdowns = [f.metrics["max_drawdown_pct"] for f in folds]  # zero or negative
        trades = [f.metrics["num_trades"] for f in folds]
        total_trades = int(sum(trades))
        # Trade-weighted, not a plain fold average: a fold with 20 trades
        # should count more than a fold with 1 - see mean_win_rate's
        # docstring on WalkForwardSummary for why this must stay distinct
        # from pct_folds_profitable.
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
            worst_fold_drawdown_pct=float(min(drawdowns)),  # most negative = worst
            total_trades=total_trades,
            mean_win_rate=float(mean_win_rate),
        )

    def _backtest(self, model: Any, symbol: str, test_df: pd.DataFrame) -> dict:
        engine = BacktestEngineV2(
            initial_capital=10000.0, commission=self.commission_rate, slippage=self.slippage_rate
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
        """Persist a compact snapshot of what the just-promoted model was
        actually trained on, so a later live drift check has something to
        compare against. Skipped (not an error) when there's no journal to
        persist to, same convention as every other optional-journal path
        in this class."""
        if self.journal is None:
            return
        distributions = {
            col: compute_baseline_distribution(featured[col])
            for col in FEATURE_COLUMNS
            if col in featured.columns
        }
        # A feature with no usable spread (e.g. constant over the training
        # window) yields None - keep it out of the persisted baseline
        # rather than storing a distribution that can never be compared
        # against.
        distributions = {col: dist for col, dist in distributions.items() if dist}
        if distributions:
            self.journal.record_feature_baseline(symbol=symbol, boundaries=distributions)

    def check_feature_drift(self, symbol: str, live_features: pd.DataFrame) -> Optional[dict]:
        """Compare a recent window of LIVE engineered features against the
        currently-promoted model's training-time baseline for this symbol.

        Returns None when there's no baseline yet to compare against (no
        journal, or no model has been promoted for this symbol) - a
        distinct, honest "nothing to check" state, not silently reported
        as "no drift". Otherwise returns and persists a drift summary;
        logs a warning when the worst-drifted feature reaches at least
        moderate severity, since that's the first sign a promoted model
        may be seeing live conditions its training data never covered.
        """
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
                severity = "critical" if summary["overall_severity"] == "significant" else "warning"
                self.alert_manager.raise_alert(
                    "feature_drift",
                    severity,
                    f"{symbol}: live feature distribution drifted from training baseline, worst feature {worst_desc}",
                    context=summary,
                    dedupe_key=f"feature_drift:{symbol}",
                )

        return summary

    def check_live_decay(self, symbol: str, recent_trade_pnls: List[float]) -> Optional[dict]:
        """Compares a symbol's REAL recent trade outcomes against what its
        currently-promoted model validated at during walk-forward -
        "detect performance deterioration early" from Live Monitoring &
        Model Decay, closing the loop feature drift alone leaves open
        (drift says conditions changed; this says whether that change
        actually hurt real results).

        Deliberately compares WIN RATE (fraction of profitable trades),
        not Sharpe: walk-forward's median_sharpe is annualized from daily-
        equity-curve statistics, while recent_trade_pnls is a handful of
        individual trade outcomes at whatever cadence they happened to
        close - forcing those into the same "Sharpe" units would compare
        two different sampling frequencies as if they meant the same
        thing. Win rate is dimensionless and directly comparable either way.

        On a confirmed decay (real win rate has fallen to less than
        decay_win_rate_fraction of what walk-forward found AND the recent
        window is net-losing - both conditions, not just one noisy metric
        alone), automatically rolls back to the previously-promoted model
        version via PredictionAgent.rollback_model.
        """
        baseline = self.last_promoted_metrics.get(symbol)
        if baseline is None or self.min_live_trades_for_decay_check is None:
            return None
        if len(recent_trade_pnls) < self.min_live_trades_for_decay_check:
            return None

        live_win_rate = sum(1 for p in recent_trade_pnls if p > 0) / len(recent_trade_pnls)
        # NOT pct_folds_profitable (fraction of FOLD windows net-positive) -
        # that's a different, coarser metric than a per-trade win rate; see
        # WalkForwardSummary.mean_win_rate's docstring. baseline.get(...)
        # falls back to pct_folds_profitable only for metrics persisted
        # before mean_win_rate existed, matching this system's own
        # "append, never break existing journaled records" convention.
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
