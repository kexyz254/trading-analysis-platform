"""StrategyManager: a regime-aware tournament across multiple candidate
strategies (ML models and rule-based strategies alike) per symbol.

Most systems pick one strategy, validate it once, and run it regardless of
current conditions. This is different: every registered candidate is
walk-forward validated with its performance segmented by market regime
(trending up / trending down / ranging - see agents.features.classify_regime),
and the live regime decides which validated candidate (if any) is actually
allowed to trade right now. A strategy that only works in trends doesn't get
to make decisions while the market is ranging, and vice versa - and if
NOTHING clears the bar for the current regime, that's an explicit, logged
"no champion" outcome (RegimeSelection.reason explains why), not a silent
default. In quantitative trading, not trading is often the correct decision.

Same no-leakage guarantees as LearningAgent's walk-forward validation: a
fixed-size training window rolled forward across history, each fold
training/fitting fresh on its own window and backtesting strictly forward
in time on the immediately-following, non-overlapping period.

Short-selling (allow_short=True) is backtest/validation-only here - it uses
BacktestEngineV2's existing OrderDirection.SHORT support to let the
tournament measure whether long/short beats long-only, particularly in
trending-down regimes where every long-only candidate structurally can't
win. It is NOT wired into live execution: real short-selling on Binance
means margin or futures, with real liquidation, funding, and leverage risk
that a backtest number alone doesn't capture - that requires its own
deliberate modeling before ever touching live capital.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from trading_assistant.agents.features import (
    FEATURE_COLUMNS,
    build_features,
    classify_regime,
    label_next_bar_direction,
)
from trading_assistant.backtesting.framework import BacktestEngineV2, OrderDirection, Position
from trading_assistant.market_data.feed import LiveFeed
from trading_assistant.market_data.feed_15m import FifteenMinuteFeed
from trading_assistant.ml.predictor import EnsemblePredictor, RandomForestPredictor, XGBPredictor
from trading_assistant.persistence.journal import Journal
from trading_assistant.risk.backtest_sizing import risk_based_quantity
from trading_assistant.stability import compute_stability_score
from trading_assistant.strategies.base import Signal
from trading_assistant.strategies.implementations import (
    BollingerBandsStrategy,
    CandlestickPatternStrategy,
    MACDStrategy,
    RSIMeanReversionStrategy,
    SMACrossoverStrategy,
    SmartMoneyConceptsStrategy,
    TimeSeriesMomentumStrategy,
    VWAPReversionStrategy,
)
# from trading_assistant.strategies.ml_15m_candidate import backtest_ml_15m
# -- NEW research-layer imports --
from trading_assistant.evaluation.recorder import CandidatePerformanceRecorder
from trading_assistant.persistence.champion_registry import ChampionRegistry

logger = logging.getLogger(__name__)

REGIMES = ("trending_up", "trending_down", "ranging")


def _default_ml_factory() -> Any:
    return EnsemblePredictor(
        predictors=[
            RandomForestPredictor(n_trees=100, max_depth=6),
            XGBPredictor(n_estimators=100, max_depth=4),
        ]
    )


@dataclass
class StrategyCandidate:
    name: str
    kind: str  # "ml" | "rule" | "ml_15m"
    factory: Callable[[], Any]


def default_candidates() -> List[StrategyCandidate]:
    """The out-of-the-box tournament roster: the ML ensemble plus every
    rule-based strategy already implemented in strategies.implementations -
    previously built but never actually used by the agent system."""
    return [
        StrategyCandidate("ml_ensemble", "ml", _default_ml_factory),
        StrategyCandidate("sma_crossover", "rule", SMACrossoverStrategy),
        StrategyCandidate("rsi_mean_reversion", "rule", RSIMeanReversionStrategy),
        StrategyCandidate("macd", "rule", MACDStrategy),
        StrategyCandidate("bollinger_bands", "rule", BollingerBandsStrategy),
        StrategyCandidate("candlestick_patterns", "rule", CandlestickPatternStrategy),
        StrategyCandidate("time_series_momentum", "rule", TimeSeriesMomentumStrategy),
        StrategyCandidate("smart_money_concepts", "rule", SmartMoneyConceptsStrategy),
        StrategyCandidate("vwap_reversion", "rule", VWAPReversionStrategy),
        StrategyCandidate("ml_ensemble_15m", "ml_15m", lambda: None),   # factory not used; dedicated backtester
    ]


@dataclass
class FoldRecord:
    fold_index: int
    regime: str
    metrics: dict


@dataclass
class RegimePerformance:
    regime: str
    num_folds: int = 0
    median_sharpe: float = 0.0
    pct_folds_profitable: float = 0.0
    total_trades: int = 0

    def clears_bar(self, min_folds: int, min_sharpe: float, min_pct_profitable: float) -> bool:
        return (
            self.num_folds >= min_folds
            and self.median_sharpe >= min_sharpe
            and self.pct_folds_profitable >= min_pct_profitable
        )

    def to_dict(self) -> dict:
        return {
            "regime": self.regime,
            "num_folds": self.num_folds,
            "median_sharpe": self.median_sharpe,
            "pct_folds_profitable": self.pct_folds_profitable,
            "total_trades": self.total_trades,
        }


@dataclass
class CandidateReport:
    name: str
    kind: str
    folds: List[FoldRecord] = field(default_factory=list)
    by_regime: Dict[str, RegimePerformance] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"name": self.name, "kind": self.kind, "by_regime": {r: p.to_dict() for r, p in self.by_regime.items()}}


@dataclass
class RegimeSelection:
    """An explicit "who trades this regime, and why" record - including the
    explicit "no champion" case, so a symbol with nothing validated for the
    current regime is a deliberate, auditable state rather than a silent
    default to some fallback behavior."""

    regime: str
    winner: Optional[str]
    reason: str

    def to_dict(self) -> dict:
        return {"regime": self.regime, "winner": self.winner, "reason": self.reason}


@dataclass
class TournamentResult:
    symbol: str
    candidates: List[CandidateReport] = field(default_factory=list)
    winners: Dict[str, RegimeSelection] = field(default_factory=dict)  # regime -> selection (winner may be None)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "winners": {r: s.to_dict() for r, s in self.winners.items()},
            "candidates": [c.to_dict() for c in self.candidates],
        }


class StrategyManager:
    """Runs the tournament and remembers, per symbol, which candidate (if
    any) is validated for each regime. Live integration (asking "who should
    trade right now, given the current regime") is `winner_for`."""

    # winner_history grows once per tournament run per symbol/regime -
    # much slower than a per-cycle append, but still unbounded over a
    # multi-month 24/7 run. Stability score and _consecutive_runs_held
    # only ever look at recent history, so a generous cap costs nothing
    # real; trimmed from the front (oldest first) so the most recent runs -
    # what every consumer actually reads - are always preserved.
    MAX_WINNER_HISTORY_LENGTH = 500

    def __init__(
        self,
        *,
        candidates: Optional[List[StrategyCandidate]] = None,
        train_window_bars: int = 500,
        test_window_bars: int = 100,
        target_folds: int = 10,
        min_folds_required: int = 3,
        min_sharpe_to_promote: float = 0.5,
        min_pct_folds_profitable: float = 0.5,
        buy_threshold: float = 0.6,
        sell_threshold: float = 0.4,
        allow_short: bool = False,
        journal: Optional[Journal] = None,
        feeds: Optional[Dict[str, Any]] = None,
        exchange: Optional[Any] = None,
        champion_switch_min_runs: int = 3,
        champion_switch_margin: float = 0.1,
        risk_per_trade: Optional[float] = None,
        position_stop_loss_pct: float = 0.01,
        max_position_size: float = 0.1,
        commission_rate: float = 0.001,
        slippage_rate: float = 0.0005,
        # -- NEW research-layer recorder parameters --
        candidate_performance_recorder: Optional[CandidatePerformanceRecorder] = None,
        champion_registry: Optional[ChampionRegistry] = None,
        # -- NEW 15‑minute candidate parameters --
        feed_15m: Optional[FifteenMinuteFeed] = None,
        ml_15m_config: Optional[Dict[str, Any]] = None,
        periods_per_year: float = 252,
    ):
        self.candidates = candidates if candidates is not None else default_candidates()
        self.train_window_bars = train_window_bars
        self.test_window_bars = test_window_bars
        self.target_folds = target_folds
        self.min_folds_required = min_folds_required
        self.min_sharpe_to_promote = min_sharpe_to_promote
        self.min_pct_folds_profitable = min_pct_folds_profitable
        self.buy_threshold = buy_threshold
        self.sell_threshold = sell_threshold
        self.allow_short = allow_short
        self.journal = journal
        self._feeds: Dict[str, Any] = feeds or {}
        self._exchange = exchange
        self.risk_per_trade = risk_per_trade
        self.position_stop_loss_pct = position_stop_loss_pct
        self.max_position_size = max_position_size
        self.commission_rate = commission_rate
        self.slippage_rate = slippage_rate
        self.periods_per_year = periods_per_year
        self.champion_switch_min_runs = champion_switch_min_runs
        self.champion_switch_margin = champion_switch_margin
        self.last_tournament: Dict[str, TournamentResult] = {}
        self.winner_history: Dict[str, Dict[str, List[Optional[str]]]] = {}
        self.live_instances: Dict[str, Dict[str, Any]] = {}
        # NEW: recorders (None = disabled)
        self.candidate_performance_recorder = candidate_performance_recorder
        self.champion_registry = champion_registry
        # NEW: 15‑minute candidate support
        self.feed_15m = feed_15m
        self.ml_15m_config = ml_15m_config or {}

    # ---------- existing public API (unchanged) ----------
    def winner_for(self, symbol: str, regime: str) -> Optional[str]:
        selection = self.selection_for(symbol, regime)
        return selection.winner if selection else None

    def selection_for(self, symbol: str, regime: str) -> Optional[RegimeSelection]:
        result = self.last_tournament.get(symbol)
        if result is None:
            return None
        return result.winners.get(regime)

    def stability_score(self, symbol: str, regime: str) -> Optional[float]:
        history = self.winner_history.get(symbol, {}).get(regime, [])
        return self.compute_stability_score(history)

    @staticmethod
    def compute_stability_score(history: List[Optional[str]]) -> Optional[float]:
        return compute_stability_score(history)

    def winner_history_for(self, symbol: str, regime: str) -> List[Optional[str]]:
        return list(self.winner_history.get(symbol, {}).get(regime, []))

    def run_tournament_from_live(
        self, symbol: str, timeframe: str = "1h", total_bars: Optional[int] = None, feed: Any = None
    ) -> TournamentResult:
        feed = feed or self._feeds.get(symbol) or LiveFeed(symbol=symbol, timeframe=timeframe, exchange=self._exchange)
        if total_bars is None:
            total_bars = self.train_window_bars + self.target_folds * self.test_window_bars + 50
        if hasattr(feed, "fetch_ohlcv_history"):
            ohlcv = feed.fetch_ohlcv_history(total_bars=total_bars)
        else:
            ohlcv = feed.fetch_ohlcv(limit=total_bars)
        return self.run_tournament(symbol, ohlcv)

    def compare_long_only_vs_long_short(self, symbol: str, ohlcv: pd.DataFrame) -> Dict[str, TournamentResult]:
        original_allow_short = self.allow_short
        try:
            self.allow_short = False
            long_only = self.run_tournament(symbol, ohlcv)
            self.allow_short = True
            long_short = self.run_tournament(symbol, ohlcv)
        finally:
            self.allow_short = original_allow_short
        return {"long_only": long_only, "long_short": long_short}

    def run_tournament(self, symbol: str, ohlcv: pd.DataFrame) -> TournamentResult:
        full_close = ohlcv["close"]
        full_open = ohlcv["open"]
        full_high = ohlcv["high"]
        full_low = ohlcv["low"]
        full_volume = ohlcv["volume"]
        full_timestamp = ohlcv["timestamp"]

        featured = build_features(ohlcv)
        featured["target"] = label_next_bar_direction(featured)
        featured = featured.dropna()

        result = TournamentResult(symbol=symbol)

        for candidate in self.candidates:
            report = CandidateReport(name=candidate.name, kind=candidate.kind)
            start = 0
            fold_index = 0

            while True:
                train_end = start + self.train_window_bars
                test_end = train_end + self.test_window_bars
                if test_end > len(featured):
                    break

                train_slice = featured.iloc[start:train_end]
                test_slice = featured.iloc[train_end:test_end]

                regime = classify_regime(
                    float(test_slice["sma_fast_vs_slow"].mean()),
                    float(test_slice["adx_strength"].mean()),
                )

                if candidate.kind == "ml":
                    model = candidate.factory()
                    model.train(train_slice[FEATURE_COLUMNS], train_slice["target"])
                    metrics = self._backtest_ml(model, symbol, test_slice)
                elif candidate.kind == "ml_15m":
                    metrics = {"sharpe_ratio": 0.0, "max_drawdown_pct": 0.0, "win_rate": 0.0, "num_trades": 0, "total_return_pct": 0.0, "profit_factor": 1.0}
                else:
                    strategy = candidate.factory()
                    metrics = self._backtest_rule(
                        strategy, symbol, full_close, test_slice,
                        full_open=full_open, full_high=full_high, full_low=full_low,
                        full_volume=full_volume, full_timestamp=full_timestamp,
                    )

                report.folds.append(FoldRecord(fold_index=fold_index, regime=regime, metrics=metrics))

                # -- NEW: record per-fold performance for research tracking --
                if self.candidate_performance_recorder is not None:
                    self.candidate_performance_recorder.record_fold_metrics(
                        symbol=symbol,
                        candidate_name=candidate.name,
                        fold_number=fold_index,
                        metrics=metrics,
                    )

                fold_index += 1
                start += self.test_window_bars

            report.by_regime = self._aggregate_by_regime(report.folds)
            result.candidates.append(report)

        result.winners = self._select_winners(symbol, result.candidates)
        self._record_winner_history(symbol, result)
        self._deploy_live_instances(symbol, featured)
        self.last_tournament[symbol] = result
        self._log_tournament(symbol, result)
        return result

    # ---------- helper methods (modified where noted) ----------

    def _deploy_live_instances(self, symbol: str, featured: pd.DataFrame) -> None:
        deployed: Dict[str, Any] = {}
        for candidate in self.candidates:
            if candidate.kind == "ml":
                model = candidate.factory()
                model.train(featured[FEATURE_COLUMNS], featured["target"])
                deployed[candidate.name] = model
            elif candidate.kind == "ml_15m":
                # 15m live instances are not deployed here; they are managed
                # separately and not yet used for live trading.
                deployed[candidate.name] = None
            else:
                deployed[candidate.name] = candidate.factory()
        self.live_instances[symbol] = deployed

    def live_signal(self, symbol: str, candidate_name: str, features: pd.DataFrame) -> Signal:
        instance = self.live_instances.get(symbol, {}).get(candidate_name)
        if instance is None:
            return Signal.HOLD

        candidate = next((c for c in self.candidates if c.name == candidate_name), None)
        if candidate is None:
            return Signal.HOLD

        if candidate.kind == "ml":
            probability_up = float(np.clip(instance.predict(features[FEATURE_COLUMNS].iloc[[-1]]), 0.0, 1.0)[0])
            if probability_up >= self.buy_threshold:
                return Signal.BUY
            if probability_up <= self.sell_threshold:
                return Signal.SELL
            return Signal.HOLD

        # 15m candidate live signal not implemented yet; always HOLD
        return instance.generate_signal(
            {
                "prices": features["close"],
                "open": features["open"],
                "high": features["high"],
                "low": features["low"],
                "close": features["close"],
                "volume": features["volume"],
                "timestamp": features["timestamp"],
            }
        )

    def _aggregate_by_regime(self, folds: List[FoldRecord]) -> Dict[str, RegimePerformance]:
        by_regime: Dict[str, RegimePerformance] = {}
        for regime in REGIMES:
            regime_folds = [f for f in folds if f.regime == regime]
            if not regime_folds:
                by_regime[regime] = RegimePerformance(regime=regime)
                continue
            sharpes = [f.metrics["sharpe_ratio"] for f in regime_folds]
            returns = [f.metrics["total_return_pct"] for f in regime_folds]
            trades = [f.metrics["num_trades"] for f in regime_folds]
            by_regime[regime] = RegimePerformance(
                regime=regime,
                num_folds=len(regime_folds),
                median_sharpe=float(np.median(sharpes)),
                pct_folds_profitable=sum(1 for r in returns if r > 0) / len(regime_folds),
                total_trades=int(sum(trades)),
            )
        return by_regime

    def _record_winner_history(self, symbol: str, result: TournamentResult) -> None:
        symbol_history = self.winner_history.setdefault(symbol, {regime: [] for regime in REGIMES})
        for regime in REGIMES:
            history = symbol_history[regime]
            history.append(result.winners[regime].winner)
            excess = len(history) - self.MAX_WINNER_HISTORY_LENGTH
            if excess > 0:
                del history[:excess]

    def _select_winners(self, symbol: str, candidates: List[CandidateReport]) -> Dict[str, RegimeSelection]:
        selections: Dict[str, RegimeSelection] = {}
        for regime in REGIMES:
            selections[regime] = self._select_winner_for_regime(symbol, regime, candidates)
        return selections

    def _select_winner_for_regime(
        self, symbol: str, regime: str, candidates: List[CandidateReport]
    ) -> RegimeSelection:
        qualifying: Dict[str, float] = {}  # name -> median_sharpe, only candidates clearing the bar
        any_folds = False
        for report in candidates:
            perf = report.by_regime.get(regime)
            if perf is None or perf.num_folds == 0:
                continue
            any_folds = True
            if perf.clears_bar(self.min_folds_required, self.min_sharpe_to_promote, self.min_pct_folds_profitable):
                qualifying[report.name] = perf.median_sharpe

        if not qualifying:
            reason = (
                "no candidate had any walk-forward folds classified in this regime yet"
                if not any_folds
                else (
                    f"no candidate cleared the promotion bar (min_folds={self.min_folds_required}, "
                    f"min_sharpe={self.min_sharpe_to_promote}, "
                    f"min_pct_folds_profitable={self.min_pct_folds_profitable}) for this regime"
                )
            )
            return RegimeSelection(regime=regime, winner=None, reason=reason)

        best_name = max(qualifying, key=qualifying.get)
        best_sharpe = qualifying[best_name]
        reason = f"{best_name} cleared the promotion bar with median sharpe {best_sharpe:.2f}"

        incumbent = self._current_winner(symbol, regime)
        if incumbent is not None and incumbent in qualifying and incumbent != best_name:
            held_runs = self._consecutive_runs_held(symbol, regime, incumbent)
            improvement = best_sharpe - qualifying[incumbent]
            if held_runs < self.champion_switch_min_runs or improvement < self.champion_switch_margin:
                best_name, best_sharpe = incumbent, qualifying[incumbent]
                reason = (
                    f"{incumbent} retained: held {held_runs} run(s) "
                    f"(needs {self.champion_switch_min_runs}) and the best challenger's edge "
                    f"({improvement:.2f}) is below the {self.champion_switch_margin} switch margin"
                )

        # -- NEW: record champion promotion in the research registry --
        if self.champion_registry is not None and best_name is not None:
            winner_report = next((c for c in candidates if c.name == best_name), None)
            if winner_report:
                regime_folds = [f for f in winner_report.folds if f.regime == regime]
                if regime_folds:
                    profit_factors = [f.metrics.get('profit_factor', 0.0) for f in regime_folds]
                    max_drawdowns = [f.metrics.get('max_drawdown_pct', 0.0) for f in regime_folds]
                    champion_metrics = {
                        'sharpe': best_sharpe,
                        'profit_factor': float(np.median(profit_factors)) if profit_factors else 0.0,
                        'max_drawdown': float(min(max_drawdowns)),
                        'folds_passed': len(regime_folds),
                        'p_value': None,
                    }
                    replaced = self._current_winner(symbol, regime)
                    if replaced == best_name:
                        replaced = None
                    self.champion_registry.record_promotion(
                        symbol=symbol,
                        regime=regime,
                        champion_name=best_name,
                        reason=reason,
                        metrics=champion_metrics,
                        replaced_champion=replaced,
                    )

        return RegimeSelection(regime=regime, winner=best_name, reason=reason)

    def _current_winner(self, symbol: str, regime: str) -> Optional[str]:
        history = self.winner_history.get(symbol, {}).get(regime, [])
        return history[-1] if history else None

    def _consecutive_runs_held(self, symbol: str, regime: str, name: str) -> int:
        history = self.winner_history.get(symbol, {}).get(regime, [])
        count = 0
        for winner in reversed(history):
            if winner != name:
                break
            count += 1
        return count

    # ---------- backtesting helpers (profit_factor added) ----------

    @staticmethod
    def _profit_factor_from_engine(engine: BacktestEngineV2) -> float:
        """Gross profit / gross loss from all closed trades in the engine."""
        closed = [p for p in engine.positions if p.exit_time is not None]
        if not closed:
            return 0.0
        gross_profit = sum(p.pnl for p in closed if p.pnl > 0)
        gross_loss = abs(sum(p.pnl for p in closed if p.pnl < 0))
        return gross_profit / gross_loss if gross_loss != 0 else float('inf')

    def _backtest_ml(self, model: Any, symbol: str, test_df: pd.DataFrame) -> dict:
        engine = BacktestEngineV2(
            initial_capital=10000.0, commission=self.commission_rate, slippage=self.slippage_rate,
            periods_per_year=self.periods_per_year,
        )
        predictions = np.clip(model.predict(test_df[FEATURE_COLUMNS]), 0.0, 1.0)
        position: Optional[Position] = None

        rows = list(test_df.itertuples(index=False))
        for probability_up, row in zip(predictions, rows):
            price = float(row.close)
            timestamp = getattr(row, "timestamp", None)
            if price <= 0:
                continue

            if probability_up >= self.buy_threshold:
                position = self._ensure_direction(engine, symbol, OrderDirection.LONG, position, price, timestamp)
            elif probability_up <= self.sell_threshold:
                if self.allow_short:
                    position = self._ensure_direction(engine, symbol, OrderDirection.SHORT, position, price, timestamp)
                else:
                    position = self._flatten_if_long(engine, position, price, timestamp)

            engine.update_equity({symbol: price})

        if position is not None:
            last_row = rows[-1]
            engine.close_position(position, float(last_row.close), getattr(last_row, "timestamp", None))

        metrics = self._result_to_dict(engine)
        metrics['profit_factor'] = self._profit_factor_from_engine(engine)
        return metrics

    def _backtest_rule(
        self,
        strategy: Any,
        symbol: str,
        full_close: pd.Series,
        test_df: pd.DataFrame,
        full_open: Optional[pd.Series] = None,
        full_high: Optional[pd.Series] = None,
        full_low: Optional[pd.Series] = None,
        full_volume: Optional[pd.Series] = None,
        full_timestamp: Optional[pd.Series] = None,
    ) -> dict:
        engine = BacktestEngineV2(
            initial_capital=10000.0, commission=self.commission_rate, slippage=self.slippage_rate,
            periods_per_year=self.periods_per_year,
        )
        position: Optional[Position] = None

        for abs_idx, row in zip(test_df.index, test_df.itertuples(index=False)):
            price = float(row.close)
            timestamp = getattr(row, "timestamp", None)
            if price <= 0:
                continue

            data = {"prices": full_close.iloc[: abs_idx + 1], "close": full_close.iloc[: abs_idx + 1]}
            if full_open is not None:
                data["open"] = full_open.iloc[: abs_idx + 1]
            if full_high is not None:
                data["high"] = full_high.iloc[: abs_idx + 1]
            if full_low is not None:
                data["low"] = full_low.iloc[: abs_idx + 1]
            if full_volume is not None:
                data["volume"] = full_volume.iloc[: abs_idx + 1]
            if full_timestamp is not None:
                data["timestamp"] = full_timestamp.iloc[: abs_idx + 1]
            signal = strategy.generate_signal(data)

            if signal == Signal.BUY:
                position = self._ensure_direction(engine, symbol, OrderDirection.LONG, position, price, timestamp)
            elif signal == Signal.SELL:
                if self.allow_short:
                    position = self._ensure_direction(engine, symbol, OrderDirection.SHORT, position, price, timestamp)
                else:
                    position = self._flatten_if_long(engine, position, price, timestamp)

            engine.update_equity({symbol: price})

        if position is not None:
            last_row = test_df.iloc[-1]
            engine.close_position(position, float(last_row["close"]), last_row.get("timestamp"))

        metrics = self._result_to_dict(engine)
        metrics['profit_factor'] = self._profit_factor_from_engine(engine)
        return metrics

    # ---------- 15‑minute candidate backtesting helper ----------
    def _backtest_ml_15m_candidate_disabled(
        self, symbol: str, test_slice: pd.DataFrame, candidate: StrategyCandidate,
        timestamps: Optional[pd.Series] = None,
    ) -> dict:
        """Run a single 15‑minute walk‑forward fold for the ml_ensemble_15m candidate."""
        if self.feed_15m is None:
            return {
                "sharpe_ratio": 0.0,
                "max_drawdown_pct": 0.0,
                "win_rate": 0.0,
                "num_trades": 0,
                "total_return_pct": 0.0,
                "profit_factor": 0.0,
            }
        # The 1h test slice index carries timestamps. Use them to define
        # the corresponding calendar windows for the 15‑minute feed.
        if timestamps is not None:
            test_start = timestamps.iloc[int(test_slice.index[0])]
            test_end = timestamps.iloc[int(test_slice.index[-1])]
        else:
            test_start = test_slice.index[0]
            test_end = test_slice.index[-1]
        test_start = pd.Timestamp(test_start)
        test_end = pd.Timestamp(test_end)
        train_start = test_start - pd.Timedelta(days=30)  # fixed 30‑day training window
        train_end = test_start

        config = {
            "horizon_bars": 4,
            "atr_multiplier": 0.5,
            "min_confidence": 0.55,
            "min_rr_ratio": 2.0,
            "fee_rate": 0.0015,
            "stop_loss_atr_mult": 2.0,
            "take_profit_atr_mult": 3.0,
        }
        config.update(self.ml_15m_config)

        return backtest_ml_15m(
            symbol=symbol,
            feed=self.feed_15m,
            train_start=train_start,
            train_end=train_end,
            test_start=test_start,
            test_end=test_end,
            config=config,
        )

    # ---------- position helpers (unchanged) ----------
    def _ensure_direction(
        self,
        engine: BacktestEngineV2,
        symbol: str,
        desired_direction: OrderDirection,
        position: Optional[Position],
        price: float,
        timestamp: Any,
    ) -> Optional[Position]:
        if position is not None and position.direction != desired_direction:
            engine.close_position(position, price, timestamp)
            position = None
        if position is None:
            if self.risk_per_trade is not None:
                quantity = risk_based_quantity(
                    engine.capital, price, self.position_stop_loss_pct,
                    risk_per_trade=self.risk_per_trade, max_position_size=self.max_position_size,
                )
            else:
                quantity = (engine.capital * 0.95) / price if price > 0 else 0.0
            if quantity > 0 and engine.open_position(symbol, desired_direction, price, timestamp, quantity=quantity):
                position = engine.positions[-1]
        return position

    @staticmethod
    def _flatten_if_long(
        engine: BacktestEngineV2, position: Optional[Position], price: float, timestamp: Any
    ) -> Optional[Position]:
        if position is not None and position.direction == OrderDirection.LONG:
            engine.close_position(position, price, timestamp)
            return None
        return position

    @staticmethod
    def _result_to_dict(engine: BacktestEngineV2) -> dict:
        result = engine.get_result()
        return {
            "sharpe_ratio": result.sharpe_ratio,
            "max_drawdown_pct": result.max_drawdown_pct,
            "win_rate": result.win_rate,
            "num_trades": result.num_trades,
            "total_return_pct": result.total_return_pct,
        }

    def _log_tournament(self, symbol: str, result: TournamentResult) -> None:
        winners_dict = {regime: selection.to_dict() for regime, selection in result.winners.items()}
        if self.journal is not None:
            self.journal.record_tournament_event(symbol=symbol, winners=winners_dict, report=result.to_dict())
        logger.info(
            "strategy tournament complete",
            extra={"context": {"symbol": symbol, "winners": winners_dict}},
        )