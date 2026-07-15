"""Orchestrator: the async decision loop coordinating every agent, one cycle at a time.

Each cycle runs a single, auditable pipeline per symbol: MarketMonitor ->
Prediction -> Risk gate -> Execution -> Portfolio update, with every step's
rationale written to the journal. This is what makes the system's behavior
inspectable rather than a black box - "reasoning" here means a structured,
logged decision trail, not literal cognition.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

import pandas as pd

from trading_assistant.agents.alerting import AlertManager
from trading_assistant.agents.champion_challenger import ChampionChallengerAgent
from trading_assistant.agents.execution import ExecutionAgent, ExecutionResult
from trading_assistant.agents.features import FEATURE_COLUMNS, build_features
from trading_assistant.agents.learning import LearningAgent
from trading_assistant.agents.market_monitor import MarketMonitorAgent
from trading_assistant.agents.portfolio import PortfolioAgent
from trading_assistant.agents.prediction import PredictionAgent, PredictionResult
from trading_assistant.agents.protective_stops import check_triggered_stop, place_protective_stop, replace_protective_stop
from trading_assistant.agents.risk import RiskAgent
from trading_assistant.agents.strategy_manager import StrategyManager
from trading_assistant.agents.symbol_discovery import SymbolDiscoveryAgent
from trading_assistant.persistence.journal import Journal
from trading_assistant.portfolio.cash_flows import CashFlowClassification
from trading_assistant.portfolio.manager import PortfolioManager
from trading_assistant.risk.partial_profit import should_take_partial_profit, unrealized_pnl_pct
from trading_assistant.risk.trailing_stop import compute_trailing_stop_price, should_raise_stop, update_high_water_mark
from trading_assistant.strategies.base import Signal

logger = logging.getLogger(__name__)

# ExecutionAgent outcomes that represent a real attempt to touch the exchange.
# "no_action" (already holding / not holding / zero-quantity guards) never
# reaches the exchange at all, so it must never count as a circuit-breaker
# "success" - otherwise a routine no-op cycle resets the consecutive-error
# counter and can mask a persistently broken execution path.
_ATTEMPTED_ACTIONS = frozenset({"opened_position", "closed_position", "partial_close", "error"})


class Orchestrator:
    def __init__(
        self,
        market_monitor: MarketMonitorAgent,
        prediction: PredictionAgent,
        risk: RiskAgent,
        execution: ExecutionAgent,
        portfolio: PortfolioAgent,
        journal: Optional[Journal] = None,
        poll_interval_seconds: float = 60.0,
        learning: Optional[LearningAgent] = None,
        retrain_interval_seconds: Optional[float] = None,
        symbol_discovery: Optional[SymbolDiscoveryAgent] = None,
        symbol_discovery_interval_seconds: Optional[float] = None,
        strategy_manager: Optional[StrategyManager] = None,
        tournament_interval_seconds: Optional[float] = None,
        champion_challenger: Optional[ChampionChallengerAgent] = None,
        drive_live_trading: bool = False,
        alert_manager: Optional[AlertManager] = None,
        # Gates PortfolioAgent.sync_capital_with_exchange in warm-start
        # (see its own docstring): real live trading must size positions
        # against the account's REAL free balance, never the
        # testnet/paper-only initial_capital constant. False (default,
        # matches every existing deployment) keeps the old testnet/paper
        # behavior unchanged - capital stays whatever initial_capital says.
        is_live: bool = False,
        quote_currency: str = "USDT",
        # Below this real (synced, see is_live above) capital, the MAIN
        # trading decision paper-trades against a dedicated fake-money
        # portfolio instead of attempting real orders that would just be
        # rejected for insufficient balance - "gather real evidence of
        # what live trading would do" using genuinely live market data
        # (this process is still really connected to the real exchange)
        # without risking capital that isn't actually there yet. Real
        # trading resumes automatically once synced capital clears this -
        # see _process_symbol/_process_live_paper_fallback.
        min_live_capital_usd: float = 5.0,
        # Position management sophistication (trading_assistant.risk.trailing_stop
        # / partial_profit): both opt-in (None disables), same convention as
        # every other new risk gate in this system.
        trailing_stop_pct: Optional[float] = None,
        partial_profit_pct: Optional[float] = None,
        partial_profit_sell_fraction: float = 0.5,
        # Trailing stop ACTIVATION threshold: the stop only starts trailing
        # once unrealized profit reaches this level (e.g. 0.02 = +2%) -
        # without this, should_raise_stop's own "only move up" guard means
        # trailing behavior emerges only incidentally, whenever the
        # trailing-implied level happens to exceed the original stop,
        # rather than at a deliberate, legible threshold. None means
        # trailing is active from the moment a position opens (the
        # previous, implicit behavior) - opt-in, backward compatible.
        trailing_stop_activation_pct: Optional[float] = None,
        # Trend exit: close a position if price falls below its own fast
        # SMA (same period as RiskAgent's trend_alignment_fast_period, by
        # convention - the entry structure this position was bought under
        # has broken). None disables entirely.
        trend_exit_sma_period: Optional[int] = None,
        # How often the FAST position-monitoring loop runs (see
        # _position_monitor_loop) - independent of, and much faster than,
        # poll_interval_seconds (the main decision cycle's cadence, 5 min
        # by default). Only touches symbols with an OPEN position, and
        # only re-evaluates trailing-stop/partial-profit/trend-exit - never
        # generates a new BUY signal or retrains anything. None disables
        # it entirely (the position management checks then only run once
        # per poll_interval_seconds, inside the main cycle, same as before
        # this existed).
        position_monitor_interval_seconds: Optional[float] = None,
        # Retention/pruning (opt-in via retention_interval_seconds, same
        # convention as retrain/discovery/tournament above): None disables
        # it entirely, matching every existing deployment's behavior before
        # this was added. See Journal.prune_old_records / _PRUNABLE_TABLES.
        retention_days: int = 180,
        retention_interval_seconds: Optional[float] = None,
    ):
        self.market_monitor = market_monitor
        self.prediction = prediction
        self.risk = risk
        self.execution = execution
        self.portfolio = portfolio
        self.journal = journal
        self.poll_interval_seconds = poll_interval_seconds
        self.learning = learning
        self.retrain_interval_seconds = retrain_interval_seconds
        self.symbol_discovery = symbol_discovery
        self.symbol_discovery_interval_seconds = symbol_discovery_interval_seconds
        self.strategy_manager = strategy_manager
        self.tournament_interval_seconds = tournament_interval_seconds
        # Champion-challenger mode (opt-in via drive_live_trading): when
        # False (the default), strategy_manager's tournament loop is purely
        # observational and every live decision still comes from
        # `prediction`/`learning`, exactly as before this was added. When
        # True, the tournament's regime winner (the "champion") drives the
        # REAL decision below - risk/execution/portfolio are unchanged,
        # only the signal source changes - while champion_challenger
        # paper-trades every OTHER candidate against the same live bar, so
        # a continuous, forward-only comparison builds up without ever
        # letting a challenger touch real execution.
        self.champion_challenger = champion_challenger
        self.drive_live_trading = drive_live_trading
        self.alert_manager = alert_manager
        self.is_live = is_live
        self.quote_currency = quote_currency
        self.min_live_capital_usd = min_live_capital_usd
        self._live_paper_portfolio: Optional[PortfolioManager] = None
        self.trailing_stop_pct = trailing_stop_pct
        self.partial_profit_pct = partial_profit_pct
        self.partial_profit_sell_fraction = partial_profit_sell_fraction
        self.trailing_stop_activation_pct = trailing_stop_activation_pct
        self.trend_exit_sma_period = trend_exit_sma_period
        self.position_monitor_interval_seconds = position_monitor_interval_seconds
        # The most recent OHLCV frame per symbol from the main cycle's own
        # market_monitor.poll() - the fast position-monitor loop reuses
        # this for SMA-based checks (trend_exit) rather than fetching a
        # fresh candle history every ~15-30s just to recompute an
        # indicator that doesn't meaningfully change within that window;
        # it still fetches a FRESH real-time price for the actual
        # trigger comparison (see _monitor_open_positions).
        self._last_snapshots: Dict[str, Any] = {}
        self.retention_days = retention_days
        self.retention_interval_seconds = retention_interval_seconds
        # Set by request_shutdown() (called from a SIGTERM/SIGINT handler
        # registered in run_system.py) - run_forever's main loop checks
        # this and exits cleanly after the CURRENT cycle finishes, never
        # mid-cycle. Without this, a supervised stop/restart (systemd,
        # Docker) sends SIGTERM, Python's default disposition kills the
        # process immediately, skipping run_forever's finally block (which
        # cleanly cancels the background tasks) and potentially
        # interrupting an in-flight order placement.
        self._shutdown_event = asyncio.Event()

    def request_shutdown(self) -> None:
        self._shutdown_event.set()

    def run_cycle(self) -> None:
        if self.is_live:
            # Checked every cycle, not just at warm-start restart, so a
            # deposit made while the process is already running gets
            # noticed (and real trading resumes) within one poll interval -
            # without this, "fund the account later and it starts trading
            # automatically" would actually require a manual restart every
            # time. Cheap: one fetch_balance call per cycle (every
            # poll_interval_seconds, default 300s), negligible API load.
            classification = self.portfolio.sync_capital_with_exchange(self.execution.exchange, self.quote_currency)
            self._maybe_handle_external_capital_flow(classification)

        snapshots = self.market_monitor.poll()
        # Cached for the fast position-monitor loop (see
        # _monitor_open_positions) - a plain dict assignment is atomic
        # enough not to need locking against that loop reading it from a
        # different thread (asyncio.to_thread), and a slightly-stale
        # snapshot is fine for an indicator (SMA) that doesn't move
        # meaningfully within one poll_interval_seconds anyway.
        self._last_snapshots = snapshots

        # -- NEW: Persist the latest OHLCV bar per symbol for the professional dashboard --
        if self.journal is not None:
            for symbol, snap in snapshots.items():
                last = snap.frame.iloc[-1]
                try:
                    self.journal.record_ohlcv_snapshot(
                        symbol=symbol,
                        timeframe=self.market_monitor.timeframe,
                        timestamp=last["timestamp"].isoformat(),
                        open=float(last["open"]),
                        high=float(last["high"]),
                        low=float(last["low"]),
                        close=float(last["close"]),
                        volume=float(last["volume"]),
                    )
                except Exception:
                    logger.exception("Failed to persist OHLCV snapshot for %s", symbol)
        # ----------------------------------------------------------------------------

        # A persistent market-data outage must be able to trip the breaker
        # just like execution failures do - otherwise the system silently
        # loops forever on a dead feed with no escalation.
        for symbol in self.market_monitor.failed_symbols:
            self.risk.circuit_breaker.record_result(symbol, success=False)

        current_prices = {symbol: float(snap.frame["close"].iloc[-1]) for symbol, snap in snapshots.items()}
        # Reuses OHLCV already fetched this cycle for the correlation-aware
        # exposure check in RiskAgent.evaluate_buy - no extra API calls.
        price_history = {symbol: snap.frame["close"] for symbol, snap in snapshots.items()}

        if current_prices:
            # Feed this cycle's real mark-to-market equity to the risk agent
            # BEFORE evaluating any symbol's BUY decision, so drawdown-based
            # position scaling (if enabled) reflects equity as of THIS
            # cycle, not whatever it was one cycle ago.
            self.risk.update_equity(self.portfolio.portfolio.get_total_value(current_prices))

        # Same "feed every cycle, not just when a BUY happens" reasoning as
        # update_equity above - reuses this cycle's already-fetched OHLCV,
        # no extra API call. .get(...) is None if the crisis symbol isn't
        # in this cycle's discovered universe for some reason;
        # update_crisis_state handles None safely (no-op).
        crisis_snapshot = snapshots.get(self.risk.crisis_symbol)
        self.risk.update_crisis_state(crisis_snapshot.frame if crisis_snapshot is not None else None)

        for symbol, snapshot in snapshots.items():
            try:
                self._process_symbol(symbol, snapshot, current_prices, price_history)
            except Exception:  # noqa: BLE001 - one symbol's failure (e.g. a stale/
                # incompatible model file) must not also skip every other
                # symbol still left in this cycle's loop.
                logger.exception("processing symbol failed", extra={"context": {"symbol": symbol}})

        if current_prices:
            self.portfolio.mark_to_market(current_prices)

    def emergency_flatten_all(self, reason: str) -> Dict[str, ExecutionResult]:
        """The genuine "kill switch" the go-live checklist calls for -
        immediately market-sells EVERY open real position and trips the
        circuit breaker, rather than the partial protections that already
        exist: the circuit breaker alone only blocks NEW positions
        (existing ones stay open), and drawdown-based risk scaling only
        reduces the size of NEW positions.

        Deliberately NOT wired to fire automatically anywhere - force-
        liquidating every position during what might be a transient data
        glitch or a temporary drawdown could itself cause real, avoidable
        losses; the circuit breaker already halts new risk-taking without
        needing to touch positions that might simply recover. This is a
        human-invoked action (see scripts/emergency_flatten.py), the same
        trust boundary CircuitBreaker.reset() already relies on - whoever
        has shell access to run this process already has full control.
        """
        if not self.risk.circuit_breaker.tripped:
            self.risk.circuit_breaker.trip(reason)

        symbols_held = list(self.portfolio.portfolio.positions.keys())
        results: Dict[str, ExecutionResult] = {}
        if not symbols_held:
            logger.warning("emergency flatten invoked with no open positions", extra={"context": {"reason": reason}})
            return results

        snapshots = self.market_monitor.poll()
        for symbol in symbols_held:
            snapshot = snapshots.get(symbol)
            if snapshot is None:
                logger.error(
                    "emergency flatten could not get a fresh price for this symbol - "
                    "position left open, needs manual attention",
                    extra={"context": {"symbol": symbol, "reason": reason}},
                )
                continue
            price = float(snapshot.frame["close"].iloc[-1])
            capital_before_trade = self.portfolio.capital
            result = self.execution.execute(symbol, Signal.SELL, price, 0.0)
            results[symbol] = result
            self._maybe_alert_unprotected_position(symbol, result)
            self._maybe_alert_unsellable_dust_position(symbol, result)
            if result.action == "closed_position":
                self.portfolio.record_trade(symbol, "sell", price, result.quantity, pnl=result.pnl)
                self._record_execution_quality(symbol, "sell", price, result)
                if result.pnl is not None:
                    self.risk.record_realized_pnl(result.pnl, capital_before_trade)
                self._record_trade_audit_close(
                    symbol, price, result.quantity,
                    exit_reason=f"emergency flatten: {reason}",
                    realized_pnl=result.pnl,
                )
            if self.journal is not None:
                self.journal.record_decision(
                    symbol=symbol,
                    signal="EMERGENCY_FLATTEN",
                    confidence=None,
                    risk_approved=True,
                    risk_reason=reason,
                    action=result.action,
                    price=price,
                    quantity=result.quantity,
                    details={"pnl": result.pnl, "emergency": True},
                )

        logger.error(
            "EMERGENCY FLATTEN executed", extra={"context": {"reason": reason, "symbols": list(results.keys())}}
        )
        if self.alert_manager is not None:
            self.alert_manager.raise_alert(
                "emergency_flatten",
                "critical",
                f"Emergency flatten invoked: {reason}. Attempted to close {len(results)} position(s).",
                context={"reason": reason, "results": {s: r.action for s, r in results.items()}},
            )
        return results

    def flatten_unexplained_positions(self, reason: str) -> Dict[str, ExecutionResult]:
        """Sell every position that's FULLY unexplained by this system's
        own trade history (see PortfolioAgent.reconcile_with_exchange) -
        e.g. testnet faucet-seeded balances the strategy never actually
        bought. Deliberately narrower than emergency_flatten_all in two
        ways: scoped to only fully-unexplained positions (never touches a
        position the strategy genuinely opened, even a partially-explained
        one), and does NOT trip the circuit breaker - this is bookkeeping
        cleanup ("stop showing me exchange dust I never traded"), not a
        risk event, so it must not halt normal trading the way a genuine
        emergency flatten correctly does.

        Selling still goes through the normal execute/record_trade/
        trade_audit path - close_position's own accounting (see
        PortfolioManager.close_position) already correctly nets a fully-
        unexplained position's proceeds to ~$0 capital impact and ~$0
        realized PnL, so this produces an honest, auditable "these were
        liquidated, netted to zero" trail rather than silently erasing
        them from history."""
        unexplained_symbols = [
            symbol for symbol, position in self.portfolio.portfolio.positions.items()
            if position.get("unexplained_quantity", 0.0) >= position["quantity"]
        ]
        results: Dict[str, ExecutionResult] = {}
        if not unexplained_symbols:
            logger.info("flatten_unexplained_positions invoked with nothing fully unexplained to flatten")
            return results

        snapshots = self.market_monitor.poll()
        for symbol in unexplained_symbols:
            snapshot = snapshots.get(symbol)
            if snapshot is None:
                logger.error(
                    "flatten_unexplained_positions could not get a fresh price for this symbol - "
                    "position left open, needs manual attention",
                    extra={"context": {"symbol": symbol, "reason": reason}},
                )
                continue
            price = float(snapshot.frame["close"].iloc[-1])
            capital_before_trade = self.portfolio.capital
            result = self.execution.execute(symbol, Signal.SELL, price, 0.0)
            results[symbol] = result
            self._maybe_alert_unprotected_position(symbol, result)
            self._maybe_alert_unsellable_dust_position(symbol, result)
            if result.action == "closed_position":
                self.portfolio.record_trade(symbol, "sell", price, result.quantity, pnl=result.pnl)
                self._record_execution_quality(symbol, "sell", price, result)
                if result.pnl is not None:
                    self.risk.record_realized_pnl(result.pnl, capital_before_trade)
                self._record_trade_audit_close(
                    symbol, price, result.quantity,
                    exit_reason=f"flatten unexplained position: {reason}",
                    realized_pnl=result.pnl,
                )
            if self.journal is not None:
                self.journal.record_decision(
                    symbol=symbol,
                    signal="FLATTEN_UNEXPLAINED",
                    confidence=None,
                    risk_approved=True,
                    risk_reason=reason,
                    action=result.action,
                    price=price,
                    quantity=result.quantity,
                    details={"pnl": result.pnl, "unexplained_cleanup": True},
                )

        logger.info(
            "flattened unexplained positions", extra={"context": {"reason": reason, "symbols": list(results.keys())}}
        )
        return results

    def _process_symbol(self, symbol: str, snapshot, current_prices: dict, price_history: Optional[dict] = None) -> None:
        price = current_prices[symbol]

        # A resting exchange-side protective stop can fill at any time,
        # independent of this loop - if it already has, this process's
        # in-memory portfolio would otherwise keep believing the position is
        # still open (corrupting capital/equity tracking) until a future
        # SELL decision fails with insufficient balance. Check and reconcile
        # BEFORE evaluating any new signal for this symbol this cycle.
        if self._reconcile_triggered_stop(symbol, price):
            return

        # Position management on whatever's still open, BEFORE evaluating a
        # new signal this cycle: retry a protective stop that failed to
        # place initially, raise the trailing stop if price has advanced,
        # take partial profit if the threshold has been crossed, and exit
        # entirely if the entry trend structure has broken. All four are
        # no-ops when a position isn't open or the relevant feature isn't
        # configured (opt-in). Trend exit is checked LAST and returns early
        # if it fired - nothing else this cycle should act on a symbol that
        # was just fully closed.
        self._retry_missing_protective_stop(symbol)
        self._update_trailing_stop(symbol, price)
        self._maybe_take_partial_profit(symbol, price)
        if self._maybe_trend_exit(symbol, price, snapshot.frame["close"]):
            return

        # Live prediction must see the exact same engineered features the model
        # was trained on (see agents.features) - passing raw OHLCV here would
        # silently break the moment LearningAgent promotes a trained model.
        features = build_features(snapshot.frame)

        if self.learning is not None:
            # Cheap, vectorized, zero extra API calls (reuses this cycle's
            # already-fetched OHLCV) - checked every cycle rather than only
            # at retrain time so a drifted feature distribution surfaces as
            # an early warning well before the next scheduled retrain
            # (which could be up to retrain_interval_seconds away) happens
            # to catch it.
            try:
                self.learning.check_feature_drift(symbol, features.dropna(subset=FEATURE_COLUMNS))
            except Exception:  # noqa: BLE001 - a drift-check failure must not block trading
                logger.exception("feature drift check failed", extra={"context": {"symbol": symbol}})

            if self.journal is not None:
                # Real, closed-trade outcomes for this symbol - "detect
                # performance deterioration early" using what actually
                # happened, not just a leading indicator like drift.
                try:
                    recent_trades = self.journal.recent_trades(symbol=symbol, limit=self.learning.decay_lookback_trades)
                    recent_pnls = [t["pnl"] for t in recent_trades if t.get("pnl") is not None]
                    self.learning.check_live_decay(symbol, recent_pnls)
                except Exception:  # noqa: BLE001 - a decay-check failure must not block trading
                    logger.exception("live decay check failed", extra={"context": {"symbol": symbol}})

        champion_name = None
        sitting_out = snapshot.market_condition is not None

        if sitting_out:
            # Deliberate sit-out: current conditions ("panic" - extreme ATR
            # spike, or "illiquid" - volume far below normal) are too
            # untrustworthy to risk real money on regardless of what signal
            # the champion/model would otherwise produce. Same "sit-out is a
            # deliberate decision" philosophy applied elsewhere (no champion
            # for this regime, no candidate clearing the significance bar) -
            # see classify_market_condition's docstring for why this is an
            # overlay rather than a new regime.
            logger.info(
                "sitting out due to market condition",
                extra={"context": {"symbol": symbol, "market_condition": snapshot.market_condition}},
            )
            prediction = PredictionResult(symbol=symbol, signal=Signal.HOLD, confidence=None)
        elif self.drive_live_trading and self.strategy_manager is not None:
            # Champion-challenger mode: the tournament's validated winner
            # for the CURRENT regime drives the real decision below. No
            # winner for this regime means the honest answer is to sit out,
            # same philosophy as everywhere else in this system - never
            # fall back to some other candidate just to have something to do.
            champion_name = self.strategy_manager.winner_for(symbol, snapshot.regime)
            champion_signal = (
                self.strategy_manager.live_signal(symbol, champion_name, features)
                if champion_name is not None
                else Signal.HOLD
            )
            prediction = PredictionResult(symbol=symbol, signal=champion_signal, confidence=None)
        else:
            prediction = self.prediction.predict(symbol, features)

        if self.champion_challenger is not None:
            # Runs regardless of drive_live_trading: while the tournament is
            # still observation-only (the common, safer state), nothing else
            # trades these candidates for real, so exclude_champion=False
            # paper-trades all of them - once drive_live_trading is actually
            # turned on, the champion trades for real above and must be
            # excluded here to avoid double-counting.
            self.champion_challenger.process_cycle(
                symbol, snapshot.regime, features, price, exclude_champion=self.drive_live_trading,
                sitting_out=sitting_out,
            )

        if self.is_live and not sitting_out and self.portfolio.capital < self.min_live_capital_usd:
            # Real capital can't back a real trade right now - paper-trade
            # this decision instead of falling through to the real path
            # below (which would just get "no capital available" from
            # RiskAgent anyway). See _process_live_paper_fallback's
            # docstring.
            self._process_live_paper_fallback(symbol, prediction, price, snapshot, champion_name, price_history)
            return

        action = "sit_out" if sitting_out else "no_action"
        quantity = 0.0
        pnl = None
        risk_approved = not sitting_out
        risk_reason = f"market_condition:{snapshot.market_condition}" if sitting_out else None

        if prediction.signal == Signal.BUY:
            positions_df = self.portfolio.positions_frame(current_prices)
            verdict = self.risk.evaluate_buy(
                symbol, price, self.portfolio.capital, positions_df,
                price_history=price_history, ohlcv_frame=snapshot.frame,
                exchange_min_notional=self._exchange_min_notional(symbol),
            )
            risk_approved, risk_reason = verdict.approved, verdict.reason
            if verdict.approved:
                exec_quantity = verdict.quantity / price if price > 0 else 0.0
                result = self.execution.execute(
                    symbol, prediction.signal, price, exec_quantity, stop_loss_pct=verdict.stop_loss_pct
                )
                if result.action in _ATTEMPTED_ACTIONS:
                    self.risk.circuit_breaker.record_result(symbol, success=result.action != "error")
                action, quantity = result.action, result.quantity
                if result.action == "opened_position":
                    self.portfolio.record_trade(symbol, "buy", price, quantity)
                    self._record_execution_quality(symbol, "buy", price, result)
                self._maybe_alert_unprotected_position(symbol, result)

        elif prediction.signal == Signal.SELL:
            verdict = self.risk.evaluate_sell(symbol)
            risk_approved, risk_reason = verdict.approved, verdict.reason
            if verdict.approved:
                # Capture pre-trade capital before execute() closes the
                # position and mutates it - the daily-loss fraction must be
                # measured against what was at risk before the trade, not
                # the post-close balance that already includes proceeds.
                capital_before_trade = self.portfolio.capital
                result = self.execution.execute(symbol, prediction.signal, price, 0.0)
                if result.action in _ATTEMPTED_ACTIONS:
                    self.risk.circuit_breaker.record_result(symbol, success=result.action != "error")
                action, quantity, pnl = result.action, result.quantity, result.pnl
                if result.action == "closed_position":
                    self.portfolio.record_trade(symbol, "sell", price, quantity, pnl=pnl)
                    self._record_execution_quality(symbol, "sell", price, result)
                    if pnl is not None:
                        self.risk.record_realized_pnl(pnl, capital_before_trade)
                    self._record_trade_audit_close(
                        symbol, price, quantity,
                        exit_reason=f"SELL signal (regime={snapshot.regime}, champion={champion_name})",
                        realized_pnl=pnl,
                    )
                self._maybe_alert_unprotected_position(symbol, result)
                self._maybe_alert_unsellable_dust_position(symbol, result)

        if self.journal is not None:
            self.journal.record_decision(
                symbol=symbol,
                signal=prediction.signal.name,
                confidence=prediction.confidence,
                risk_approved=risk_approved,
                risk_reason=risk_reason,
                action=action,
                price=price,
                quantity=quantity,
                details={
                    "regime": snapshot.regime,
                    "rsi": snapshot.rsi,
                    "pnl": pnl,
                    "champion": champion_name,
                    "probability_up": prediction.probability_up,
                    "market_condition": snapshot.market_condition,
                },
            )

    def _get_or_create_live_paper_portfolio(self) -> PortfolioManager:
        if self._live_paper_portfolio is None:
            real = self.portfolio.portfolio
            self._live_paper_portfolio = PortfolioManager(
                initial_capital=real.initial_capital,
                commission_rate=real.commission_rate,
                slippage_rate=real.slippage_rate,
                periods_per_year=real.periods_per_year,
            )
        return self._live_paper_portfolio

    def _process_live_paper_fallback(
        self,
        symbol: str,
        prediction: PredictionResult,
        price: float,
        snapshot,
        champion_name: Optional[str],
        price_history: Optional[dict] = None,
    ) -> None:
        """Live mode with real capital below min_live_capital_usd: this
        process is genuinely connected to the real exchange (real market
        data, real predictions) - only EXECUTION is faked, against a
        dedicated in-memory paper portfolio, never a real order. Runs the
        SAME RiskAgent gating a real trade would face (evaluate_buy/
        evaluate_sell take capital/positions as plain arguments, not
        internal state, so passing the paper portfolio's own numbers here
        never touches anything real) rather than a simplified sizing
        preview, so the resulting paper track record is a faithful
        rehearsal of what real trading would actually do once funded, not
        a toy that fires on every signal regardless of risk gates.

        Distinct from ChampionChallengerAgent (which paper-trades every
        TOURNAMENT candidate, always, regardless of capital) - this is
        specifically the one MAIN strategy decision, paper-traded only
        because real funds can't back it yet. Resets to a fresh paper
        portfolio on every process restart (nothing to reconcile a purely
        simulated balance against) - a known, accepted limitation, not
        persisted like the real portfolio's exchange-backed state is.
        """
        portfolio = self._get_or_create_live_paper_portfolio()
        action = "no_action"
        quantity = 0.0
        pnl = None
        risk_approved = False
        risk_reason = None

        if prediction.signal == Signal.BUY:
            positions_df = pd.DataFrame(
                [
                    {"symbol": sym, "quantity": pos["quantity"], "price": pos["price"]}
                    for sym, pos in portfolio.positions.items()
                ],
                columns=["symbol", "quantity", "price"],
            )
            verdict = self.risk.evaluate_buy(
                symbol, price, portfolio.capital, positions_df,
                price_history=price_history, ohlcv_frame=snapshot.frame,
                exchange_min_notional=self._exchange_min_notional(symbol),
            )
            risk_approved, risk_reason = verdict.approved, verdict.reason
            if verdict.approved:
                exec_quantity = verdict.quantity / price if price > 0 else 0.0
                if exec_quantity > 0 and portfolio.open_position(symbol, price, exec_quantity):
                    action, quantity = "opened_position", exec_quantity

        elif prediction.signal == Signal.SELL:
            if symbol in portfolio.positions:
                risk_approved = True
                quantity = portfolio.positions[symbol]["quantity"]
                pnl = portfolio.close_position(symbol, price)
                action = "closed_position"
            else:
                risk_reason = "no open paper position to sell"

        total_value = portfolio.get_total_value({symbol: price})
        portfolio.append_equity_point(total_value)

        if self.journal is not None:
            self.journal.record_decision(
                symbol=symbol,
                signal=prediction.signal.name,
                confidence=prediction.confidence,
                risk_approved=risk_approved,
                risk_reason=risk_reason,
                action=action,
                price=price,
                quantity=quantity,
                details={
                    "paper": True,
                    "candidate": "live_fallback",
                    "reason": "real capital below min_live_capital_usd",
                    "regime": snapshot.regime,
                    "pnl": pnl,
                    "champion": champion_name,
                    "probability_up": prediction.probability_up,
                },
            )
            self.journal.record_paper_equity_snapshot(
                symbol=symbol,
                candidate="live_fallback",
                capital=portfolio.capital,
                total_equity=total_value,
                num_trades=len(portfolio.trade_history),
            )

    def _exchange_min_notional(self, symbol: str) -> Optional[float]:
        """The real exchange's own minimum order value for `symbol` (e.g.
        Binance's MIN_NOTIONAL filter), for RiskAgent.evaluate_buy's
        exchange_min_notional param - see RiskConfig.min_stake_usd's
        docstring for why this matters (a $1 system-level floor isn't
        enough on its own if THIS symbol's real exchange minimum happens
        to be higher). None on any lookup failure (no exchange, market not
        loaded, unexpected market structure) - callers fall back to just
        min_stake_usd in that case, never crash a cycle over this."""
        if self.execution.exchange is None:
            return None
        try:
            market = self.execution.exchange.market(symbol)
            return market.get("limits", {}).get("cost", {}).get("min")
        except Exception:  # noqa: BLE001 - a lookup failure must not block trading
            logger.exception("could not look up exchange minimum notional", extra={"context": {"symbol": symbol}})
            return None

    def _retry_missing_protective_stop(self, symbol: str) -> None:
        """Retry placing a protective stop for a real, currently-open
        position whose initial placement failed (position['stop_order_id']
        is None). Real incident: a live BTC/USDT buy filled correctly, but
        the protective stop failed with InsufficientFunds (the exchange's
        ~0.1% trading fee, taken out of the base asset received, plus
        rounding down to the exchange's lot-size step, left slightly less
        than the requested stop quantity) - and PREVIOUSLY there was no
        automatic retry at all until the next full process restart
        (reconcile_with_exchange only runs once, at warm start), meaning a
        real position could sit genuinely unprotected indefinitely under
        24/7 unattended operation. Runs every cycle instead, using the
        exchange's ACTUAL current free balance (already fee-adjusted) so
        it self-heals the moment the exchange will actually accept it -
        e.g. once price has moved enough to clear the minimum notional
        again, rather than only on the next restart."""
        if self.execution.exchange is None:
            return
        position = self.portfolio.portfolio.positions.get(symbol)
        if position is None or position.get("stop_order_id") is not None:
            return
        effective_stop_pct = position.get("intended_stop_loss_pct")
        entry_price = position.get("price")
        if effective_stop_pct is None or not entry_price:
            return
        base_asset = symbol.split("/")[0]
        try:
            balance = self.execution.exchange.fetch_balance()
        except Exception:  # noqa: BLE001 - a failed balance check must not crash a cycle; try again next cycle
            logger.exception("could not fetch balance while retrying a missing protective stop", extra={"context": {"symbol": symbol}})
            return
        free_qty = float((balance.get(base_asset) or {}).get("free", 0.0) or 0.0)
        if free_qty <= 0:
            return
        order_id = place_protective_stop(self.execution.exchange, symbol, free_qty, entry_price, effective_stop_pct)
        if order_id is not None:
            position["stop_order_id"] = order_id
            position["stop_price"] = entry_price * (1 - effective_stop_pct)
            logger.info(
                "protective stop backfilled on retry - position is now protected",
                extra={"context": {"symbol": symbol, "quantity": free_qty}},
            )
            if self.alert_manager is not None:
                self.alert_manager.raise_alert(
                    "position_unprotected",
                    "info",
                    f"{symbol}: protective stop backfilled successfully - position is now protected.",
                    context={"symbol": symbol},
                    dedupe_key=f"position_unprotected:{symbol}",
                )

    def _reconcile_triggered_stop(self, symbol: str, current_price: float) -> bool:
        """If this symbol has an open real position protected by a resting
        stop-loss order, check whether the exchange already filled it.
        Returns True if a triggered stop was found and reconciled (the
        caller should treat this symbol as done for this cycle - the next
        cycle decides fresh on a genuinely flat position)."""
        if self.execution.exchange is None:
            return False
        position = self.portfolio.portfolio.positions.get(symbol)
        if position is None:
            return False
        stop_order_id = position.get("stop_order_id")
        if not stop_order_id:
            return False

        triggered_order = check_triggered_stop(self.execution.exchange, symbol, stop_order_id)
        if triggered_order is None:
            return False

        quantity = position["quantity"]
        fill_price = float(triggered_order.get("average") or triggered_order.get("price") or current_price)
        capital_before_trade = self.portfolio.capital
        pnl = self.portfolio.portfolio.close_position(symbol, fill_price)
        if pnl is not None:
            self.portfolio.record_trade(symbol, "sell", fill_price, quantity, pnl=pnl)
            self.risk.record_realized_pnl(pnl, capital_before_trade)
        self._record_trade_audit_close(
            symbol, fill_price, quantity,
            exit_reason=f"exchange-side protective stop-loss triggered (stop_order_id={stop_order_id})",
            realized_pnl=pnl,
        )

        logger.warning(
            "protective stop-loss triggered - position closed by the exchange, local state reconciled",
            extra={"context": {"symbol": symbol, "fill_price": fill_price, "quantity": quantity, "pnl": pnl}},
        )
        if self.alert_manager is not None:
            # Each trigger is its own distinct real-money event (a specific
            # position closing) - no dedupe_key, every one should notify.
            self.alert_manager.raise_alert(
                "stop_loss_triggered",
                "warning",
                f"{symbol}: protective stop-loss triggered, position closed at {fill_price} (pnl={pnl})",
                context={"symbol": symbol, "fill_price": fill_price, "quantity": quantity, "pnl": pnl},
            )
        if self.journal is not None:
            self.journal.record_decision(
                symbol=symbol,
                signal="STOP_LOSS_TRIGGERED",
                confidence=None,
                risk_approved=True,
                risk_reason="exchange-side protective stop filled",
                action="closed_position",
                price=fill_price,
                quantity=quantity,
                details={"pnl": pnl, "stop_order_id": stop_order_id},
            )
        return True

    def _update_trailing_stop(self, symbol: str, current_price: float) -> None:
        """Raise a real position's resting protective stop as price
        advances (see trading_assistant.risk.trailing_stop). A no-op
        unless trailing_stop_pct is configured, there's an exchange to
        place orders on, and this symbol already has an open position
        protected by a resting stop - nothing to trail otherwise."""
        if self.execution.exchange is None or self.trailing_stop_pct is None:
            return
        position = self.portfolio.portfolio.positions.get(symbol)
        if position is None:
            return
        stop_order_id = position.get("stop_order_id")
        if not stop_order_id:
            return

        # Activation threshold: don't start trailing at all until unrealized
        # profit clears this level - without it, trailing only emerged
        # incidentally whenever the trailing-implied level happened to
        # exceed the original stop (an accident of should_raise_stop's
        # "only move up" guard), not at a deliberate, legible threshold.
        # None (disabled) preserves that original "always trail" behavior.
        if self.trailing_stop_activation_pct is not None:
            entry_price = position.get("price")
            if entry_price and unrealized_pnl_pct(entry_price, current_price) < self.trailing_stop_activation_pct:
                return

        high_water_mark = update_high_water_mark(position.get("high_water_mark"), current_price)
        position["high_water_mark"] = high_water_mark
        candidate_stop_price = compute_trailing_stop_price(high_water_mark, self.trailing_stop_pct)
        current_stop_price = position.get("stop_price")

        if not should_raise_stop(current_stop_price, candidate_stop_price):
            return

        # Atomic cancel-replace (see protective_stops.replace_protective_stop):
        # the exchange only ever cancels the OLD stop once the NEW one is
        # confirmed placed, so a rejected replacement leaves the position
        # exactly as protected as it was before this call - never worse.
        new_order_id = replace_protective_stop(
            self.execution.exchange, symbol, stop_order_id, position["quantity"], candidate_stop_price
        )
        if new_order_id is not None:
            position["stop_order_id"] = new_order_id
            position["stop_price"] = candidate_stop_price
            logger.info(
                "trailing stop raised",
                extra={
                    "context": {
                        "symbol": symbol,
                        "new_stop_price": candidate_stop_price,
                        "high_water_mark": high_water_mark,
                    }
                },
            )
        else:
            # Deliberately leave position['stop_order_id']/['stop_price']
            # UNCHANGED - the existing stop is still resting on the
            # exchange (see replace_protective_stop's docstring), so the
            # next cycle will naturally retry raising it to a fresh
            # candidate_stop_price computed from the current high water
            # mark, rather than this position being silently abandoned
            # forever the moment one replacement attempt fails.
            logger.warning(
                "failed to raise trailing stop - the existing stop remains in place, will retry next cycle",
                extra={"context": {"symbol": symbol, "attempted_stop_price": candidate_stop_price}},
            )
            if self.alert_manager is not None:
                self.alert_manager.raise_alert(
                    "trailing_stop_raise_failed",
                    "warning",
                    f"{symbol}: failed to raise trailing stop to {candidate_stop_price}, "
                    f"the existing stop at {current_stop_price} remains in place",
                    context={
                        "symbol": symbol,
                        "attempted_stop_price": candidate_stop_price,
                        "current_stop_price": current_stop_price,
                    },
                    dedupe_key=f"trailing_stop_raise_failed:{symbol}",
                )

    def _maybe_handle_external_capital_flow(self, classification: Optional[CashFlowClassification]) -> None:
        """A real incident this exists for: moving funds into Binance's
        Predictions feature dropped free balance by 99.6% with zero trading
        activity, and the drawdown circuit breaker correctly (by its own
        logic) treated that as a catastrophic loss and halted trading. It
        had no way to know the difference between "the bot lost the money"
        and "the account holder moved it elsewhere" - this closes that gap.
        Rebases RiskAgent's drawdown peak by the unexplained amount (so a
        withdrawal/deposit/transfer never registers as trading performance)
        and raises a low-severity alert so it's never silently invisible."""
        if classification is None or not classification.is_external_flow:
            return
        self.risk.adjust_peak_equity_for_external_flow(classification.unexplained_delta)
        if self.alert_manager is not None:
            direction = "increased" if classification.unexplained_delta > 0 else "decreased"
            self.alert_manager.raise_alert(
                "external_capital_flow",
                "info",
                f"Free balance {direction} by ${abs(classification.unexplained_delta):.2f} in a way this "
                f"bot's own trading doesn't explain - likely a deposit, withdrawal, or transfer to another "
                f"product. Adjusted the drawdown baseline so this isn't mistaken for a trading loss.",
                context={
                    "unexplained_delta": classification.unexplained_delta,
                    "raw_delta": classification.raw_delta,
                    "trading_delta": classification.trading_delta,
                },
            )

    def _maybe_alert_unprotected_position(self, symbol: str, result: ExecutionResult) -> None:
        """A real, currently-open position with no resting protective stop
        is direct capital-at-risk - the whole point of protective_stops.py
        is that a bad move against an unprotected position has nothing to
        catch it. ExecutionResult.stop_order_placed is False only when a
        stop was actually ATTEMPTED and failed (never for "no_action"/
        "error" outcomes that never touched a real position), so this is
        safe to call after every execute()/execute_partial_sell() result
        unconditionally. ExecutionAgent itself deliberately has no
        alert_manager - this is the one place that owns it."""
        if result.stop_order_placed is not False:
            return
        if self.alert_manager is not None:
            self.alert_manager.raise_alert(
                "position_unprotected",
                "critical",
                f"{symbol}: a real position is currently open with NO protective stop-loss on the "
                f"exchange - placing/re-placing one just failed. This position is unprotected right now.",
                context={"symbol": symbol, "action": result.action},
                dedupe_key=f"position_unprotected:{symbol}",
            )

    def _maybe_alert_unsellable_dust_position(self, symbol: str, result: ExecutionResult) -> None:
        """ExecutionAgent._sell/_execute_partial_sell refuse to even attempt
        a sell once a held position's value has drifted below the
        exchange's real minimum order value (see execution.py) - the only
        way either method returns "no_action" for a symbol that's actually
        held. Silent is the right default for the circuit breaker (this
        must never count as an error), but a human should still hear about
        it once: the position is real, stuck, and won't close on its own
        until it grows back above the minimum or someone handles it
        manually."""
        # result.error is None for the OTHER "no_action" case (nothing was
        # actually held to sell) - only the dust-skip path sets it.
        if result.action != "no_action" or result.error is None:
            return
        if self.alert_manager is not None:
            self.alert_manager.raise_alert(
                "dust_position_unsellable",
                "warning",
                f"{symbol}: {result.error}",
                context={"symbol": symbol},
                dedupe_key=f"dust_position_unsellable:{symbol}",
            )

    def _maybe_take_partial_profit(self, symbol: str, current_price: float) -> None:
        """Sell a configured fraction of an open real position once it's
        moved favorably enough (see trading_assistant.risk.partial_profit),
        locking in some gain while the remainder keeps riding under
        whatever stop already protects it. Fires at most once per
        position - `partial_profit_taken` is set the moment it fires so a
        position that stays above the threshold for many cycles doesn't
        repeatedly sell down to nothing."""
        if self.execution.exchange is None or self.partial_profit_pct is None:
            return
        position = self.portfolio.portfolio.positions.get(symbol)
        if position is None:
            return

        already_taken = bool(position.get("partial_profit_taken"))
        if not should_take_partial_profit(
            position["price"], current_price, self.partial_profit_pct, already_taken
        ):
            return

        sell_quantity = position["quantity"] * self.partial_profit_sell_fraction
        # Captured BEFORE the sell - the daily-loss fraction must be measured
        # against what was at risk before the trade, not the post-sell
        # balance that already includes the proceeds (same reasoning as the
        # full-close SELL path above).
        capital_before_trade = self.portfolio.capital
        result = self.execution.execute_partial_sell(symbol, sell_quantity, current_price)
        self._maybe_alert_unprotected_position(symbol, result)
        self._maybe_alert_unsellable_dust_position(symbol, result)
        if result.action != "partial_close":
            return

        # Mark AFTER a successful sell - a failed attempt should be free to
        # retry next cycle rather than being silently marked "done".
        remaining = self.portfolio.portfolio.positions.get(symbol)
        if remaining is not None:
            remaining["partial_profit_taken"] = True

        self.portfolio.record_trade(symbol, "sell", current_price, result.quantity, pnl=result.pnl)
        self._record_execution_quality(symbol, "sell", current_price, result)
        if result.pnl is not None:
            self.risk.record_realized_pnl(result.pnl, capital_before_trade)
        self._record_trade_audit_close(
            symbol, current_price, result.quantity,
            exit_reason=f"partial profit taken: unrealized gain crossed partial_profit_pct={self.partial_profit_pct}",
            realized_pnl=result.pnl,
        )

        logger.info(
            "partial profit taken",
            extra={"context": {"symbol": symbol, "quantity": result.quantity, "pnl": result.pnl}},
        )
        if self.journal is not None:
            self.journal.record_decision(
                symbol=symbol,
                signal="PARTIAL_PROFIT_TAKEN",
                confidence=None,
                risk_approved=True,
                risk_reason=f"unrealized gain crossed partial_profit_pct={self.partial_profit_pct}",
                action="partial_close",
                price=current_price,
                quantity=result.quantity,
                details={"pnl": result.pnl},
            )

    def _maybe_trend_exit(self, symbol: str, current_price: float, close_series: Optional[pd.Series]) -> bool:
        """Close an open real position if price has fallen below its own
        fast SMA (same period as RiskAgent's trend_alignment_fast_period,
        by convention) - the entry structure (price > SMA_fast > SMA_slow)
        this position was bought under has broken, independent of whatever
        the model/champion strategy's own signal says this cycle. A full
        close, not a partial one (see _maybe_take_partial_profit for that) -
        this is specifically the "get out, the trend that justified being
        in this trade is gone" rule, not a profit-locking mechanism.

        Returns True if this closed the position - the caller (both the
        main cycle and the fast position-monitor loop) should treat the
        symbol as done for this pass, the same convention
        _reconcile_triggered_stop already uses."""
        if self.trend_exit_sma_period is None or self.execution.exchange is None or close_series is None:
            return False
        position = self.portfolio.portfolio.positions.get(symbol)
        if position is None:
            return False
        if len(close_series) < self.trend_exit_sma_period:
            return False
        sma = close_series.rolling(self.trend_exit_sma_period).mean().iloc[-1]
        if pd.isna(sma) or current_price >= sma:
            return False

        capital_before_trade = self.portfolio.capital
        result = self.execution.execute(symbol, Signal.SELL, current_price, 0.0)
        if result.action in _ATTEMPTED_ACTIONS:
            self.risk.circuit_breaker.record_result(symbol, success=result.action != "error")
        self._maybe_alert_unprotected_position(symbol, result)
        self._maybe_alert_unsellable_dust_position(symbol, result)
        if result.action != "closed_position":
            return False

        self.portfolio.record_trade(symbol, "sell", current_price, result.quantity, pnl=result.pnl)
        self._record_execution_quality(symbol, "sell", current_price, result)
        if result.pnl is not None:
            self.risk.record_realized_pnl(result.pnl, capital_before_trade)
        exit_reason = f"trend exit: price closed below SMA{self.trend_exit_sma_period} (was {sma:.6g})"
        self._record_trade_audit_close(symbol, current_price, result.quantity, exit_reason=exit_reason, realized_pnl=result.pnl)

        logger.info(
            "trend exit - position closed",
            extra={"context": {"symbol": symbol, "price": current_price, "sma": sma, "pnl": result.pnl}},
        )
        if self.journal is not None:
            self.journal.record_decision(
                symbol=symbol,
                signal="TREND_EXIT",
                confidence=None,
                risk_approved=True,
                risk_reason=exit_reason,
                action="closed_position",
                price=current_price,
                quantity=result.quantity,
                details={"pnl": result.pnl, "sma": sma},
            )
        return True

    def _reconstruct_entry_context(self, symbol: str) -> Optional[Dict[str, Any]]:
        """The immutable trade_audit trail is written once, at close time,
        with both entry and exit context together - but entry context
        (price, confidence, regime, which strategy drove the decision) was
        only known back when the position was OPENED, potentially many
        cycles (or a restart) ago. Rather than caching that in memory (lost
        on restart, and duplicating state journal.record_decision already
        durably persisted), this reconstructs it by walking this symbol's
        decision history backward for the most recent "opened_position"
        entry - the entry this position has held continuously since,
        because a full close would have happened first otherwise and this
        method is only ever called exactly when that close is happening
        now, so the first "opened_position" found is always the right one,
        including across any number of intervening partial closes."""
        if self.journal is None:
            return None
        for decision in self.journal.recent_decisions(symbol=symbol, limit=200):
            if decision.get("action") != "opened_position":
                continue
            details = decision.get("details") or {}
            champion = details.get("champion")
            # A tournament candidate has no on-disk versioning concept
            # (rule strategies are stateless code, live ML candidates are
            # in-memory only) - None here is honest, not a bug. Only the
            # standalone PredictionAgent path persists a versionable model
            # file.
            strategy_name = champion if champion is not None else "ai_ensemble"
            strategy_version = None if champion is not None else self.prediction.model_version(symbol)
            return {
                "entry_price": decision.get("price"),
                "entry_timestamp": decision.get("timestamp"),
                "strategy_name": strategy_name,
                "strategy_version": strategy_version,
                "model_confidence": decision.get("confidence"),
                "market_regime": details.get("regime"),
                "entry_reason": (
                    f"{decision.get('signal')} signal from {strategy_name}, "
                    f"regime={details.get('regime')}, confidence={decision.get('confidence')}"
                ),
            }
        return None

    def _record_trade_audit_close(
        self, symbol: str, exit_price: float, quantity: float, exit_reason: str, realized_pnl: Optional[float],
    ) -> None:
        """Append one immutable trade_audit record for a real close (full
        or partial) - the trustworthy, append-only history a future
        "exactly how did this system perform" question should be answered
        from, distinct from the operational decisions/trades tables this
        reconstructs entry context from."""
        if self.journal is None:
            return
        entry_context = self._reconstruct_entry_context(symbol) or {}
        self.journal.record_trade_audit(
            symbol=symbol,
            side="long",  # spot-only, long-only live execution - see strategy_manager's allow_short docstring
            exit_price=exit_price,
            quantity=quantity,
            exit_reason=exit_reason,
            realized_pnl=realized_pnl,
            **entry_context,
        )

    def _record_execution_quality(self, symbol: str, side: str, intended_price: float, result: ExecutionResult) -> None:
        """Persist real slippage/fill-quality/latency telemetry for a
        successfully-executed order - "measure slippage by pair/order
        type" and "benchmark execution against expected fill quality"
        from the Execution Quality checklist, using data ExecutionAgent
        already resolves from the exchange's own order response but
        previously only used for portfolio bookkeeping."""
        if self.journal is None or result.slippage_pct is None:
            return
        self.journal.record_execution_quality(
            symbol=symbol,
            side=side,
            order_type=result.order_type,
            intended_price=intended_price,
            fill_price=result.price,
            slippage_pct=result.slippage_pct,
            latency_ms=result.latency_ms,
        )

    async def run_forever(self, max_cycles: Optional[int] = None, warm_start: bool = True) -> None:
        if self.symbol_discovery is not None and warm_start:
            # Run before the warm-start retrain so that retrain covers the
            # actual discovered universe, not just the static seed list.
            await asyncio.to_thread(self._refresh_symbol_universe)

        if warm_start and self.execution.exchange is not None:
            # AFTER symbol discovery (needs the full discovered universe,
            # not just the static seed list) but before anything can act:
            # PortfolioManager's positions/capital live entirely in process
            # memory with no persistence across restarts, so without this
            # every restart (including the crash-resilient supervisor's own
            # automatic ones) makes the system "forget" any real position
            # it already holds - and ExecutionAgent.execute()'s BUY guard
            # (`symbol not in self.portfolio.positions`) would then happily
            # buy AGAIN into a symbol already held for real. Found via a
            # real testnet account holding 2267 XRP while the in-memory
            # portfolio tracked only the most recent 933.
            unprotected_symbols = await asyncio.to_thread(
                self.portfolio.reconcile_with_exchange,
                self.execution.exchange,
                self.market_monitor.symbols,
                self.execution.stop_loss_pct,
            )
            # A real, currently-held position with no resting protective
            # stop is direct capital-at-risk (nothing would catch a bad
            # move against it) - loud enough to page a human, not just a
            # log line nobody's watching in real time.
            if self.alert_manager is not None:
                for symbol in unprotected_symbols:
                    self.alert_manager.raise_alert(
                        "position_unprotected",
                        "critical",
                        f"{symbol}: reconciled a real, held position with NO protective stop-loss on the "
                        f"exchange - backfilling one failed. This position is currently unprotected.",
                        context={"symbol": symbol},
                        dedupe_key=f"position_unprotected:{symbol}",
                    )

            if self.is_live:
                # See PortfolioAgent.sync_capital_with_exchange's own
                # docstring for why this is live-only: testnet deliberately
                # keeps using the configured initial_capital (a controlled
                # fictional bankroll), but a real account's tradable cash
                # must reflect what's ACTUALLY there right now, not a
                # config constant that has no relationship to the real
                # balance (which could be $0, or any amount that changes
                # independently as the operator deposits/withdraws).
                await asyncio.to_thread(
                    self.portfolio.sync_capital_with_exchange, self.execution.exchange, self.quote_currency
                )

        if self.learning is not None and warm_start:
            # Without this, a fresh deployment would run with no model - and
            # therefore never trade - until the first background retrain
            # fires, up to retrain_interval_seconds later (default 24h).
            await self._retrain_all_symbols()

        if self.strategy_manager is not None and warm_start:
            # Without this, winner_history's first data point wouldn't land
            # until tournament_interval_seconds later (default 24h) - and
            # stability_score() needs at least 2 runs to say anything at all,
            # so every warm-start skipped here is a full cycle's delay before
            # the stability track record even starts accumulating.
            await self._run_tournament_all_symbols()

        retrain_task: Optional[asyncio.Task] = None
        if self.learning is not None and self.retrain_interval_seconds:
            retrain_task = asyncio.create_task(self._retrain_loop())

        discovery_task: Optional[asyncio.Task] = None
        if self.symbol_discovery is not None and self.symbol_discovery_interval_seconds:
            discovery_task = asyncio.create_task(self._symbol_discovery_loop())

        tournament_task: Optional[asyncio.Task] = None
        if self.strategy_manager is not None and self.tournament_interval_seconds:
            tournament_task = asyncio.create_task(self._tournament_loop())

        retention_task: Optional[asyncio.Task] = None
        if self.journal is not None and self.retention_interval_seconds:
            retention_task = asyncio.create_task(self._retention_loop())

        position_monitor_task: Optional[asyncio.Task] = None
        if self.position_monitor_interval_seconds:
            position_monitor_task = asyncio.create_task(self._position_monitor_loop())

        if self.journal is not None:
            # Written once, right before the live loop begins - this is what
            # the dashboard's uptime is measured from (see trading_assistant/
            # health.py), deliberately AFTER warm-start so it reflects
            # "actually trading," not "process launched but still
            # reconciling/retraining." The dashboard runs in a separate
            # process and only ever reads what's persisted here - same
            # cross-process pattern as every other journal-backed metric.
            self.journal.record_heartbeat_start()

        try:
            cycles = 0
            while (max_cycles is None or cycles < max_cycles) and not self._shutdown_event.is_set():
                try:
                    # Offload to a worker thread: run_cycle does blocking I/O
                    # (ccxt fetches, order placement, retry backoff sleeps).
                    # Calling it directly on the event loop would freeze
                    # _retrain_loop's scheduling (and any other coroutine)
                    # for the duration of every cycle.
                    await asyncio.to_thread(self.run_cycle)
                    if self.journal is not None:
                        self.journal.record_heartbeat_cycle(cycle_number=cycles, status="ok")
                except Exception:  # noqa: BLE001 - one bad cycle must not kill a 24/7 process
                    logger.exception("cycle failed")
                    if self.journal is not None:
                        self.journal.record_heartbeat_cycle(cycle_number=cycles, status="error")
                cycles += 1
                if max_cycles is not None and cycles >= max_cycles:
                    break
                if self._shutdown_event.is_set():
                    break
                # Interruptible sleep: request_shutdown() firing mid-wait
                # (a SIGTERM arriving between cycles) wakes this up
                # immediately instead of blocking for the full
                # poll_interval_seconds (up to 5 minutes by default) before
                # the loop condition is even re-checked - matters because
                # systemd/Docker only wait a bounded grace period before
                # escalating to SIGKILL.
                try:
                    await asyncio.wait_for(self._shutdown_event.wait(), timeout=self.poll_interval_seconds)
                except asyncio.TimeoutError:
                    pass
        finally:
            if self._shutdown_event.is_set():
                logger.info("graceful shutdown requested - background loops cancelled, exiting cleanly")
            if retrain_task is not None:
                retrain_task.cancel()
            if discovery_task is not None:
                discovery_task.cancel()
            if tournament_task is not None:
                tournament_task.cancel()
            if retention_task is not None:
                retention_task.cancel()
            if position_monitor_task is not None:
                position_monitor_task.cancel()

    async def _run_supervised(self, loop_name: str, fn) -> None:
        """Shields one iteration of a background loop's body with the same
        try/except run_forever's own main cycle loop already relies on -
        without this, ANY exception escaping a background loop's iteration
        kills that asyncio.Task PERMANENTLY. Nothing in this codebase ever
        awaits these tasks or checks their result (confirmed: no
        `add_done_callback`/`.result()` anywhere), so a dead task is
        silent - retraining, the tournament, symbol discovery, or pruning
        just stops forever with no restart and no alert. `fn` may be a
        sync or async callable; both are supported so callers don't need
        their own asyncio.to_thread wrapper just to fit this shield."""
        try:
            result = fn()
            if asyncio.iscoroutine(result):
                await result
        except Exception:  # noqa: BLE001 - the whole point: never let this loop die
            logger.exception(f"{loop_name} loop iteration failed")
            if self.alert_manager is not None:
                self.alert_manager.raise_alert(
                    "background_loop_failed",
                    "critical",
                    f"{loop_name} loop iteration failed - see logs for the traceback. "
                    f"The loop itself will retry on its next scheduled run.",
                    context={"loop": loop_name},
                    dedupe_key=f"background_loop_failed:{loop_name}",
                )

    async def _retrain_loop(self) -> None:
        """Runs on its own cadence, independent of the live decision loop."""
        while True:
            await asyncio.sleep(self.retrain_interval_seconds)
            await self._run_supervised("retrain", self._retrain_all_symbols)

    async def _symbol_discovery_loop(self) -> None:
        """Runs on its own (typically daily) cadence, independent of the
        live decision loop and the retrain loop."""
        while True:
            await asyncio.sleep(self.symbol_discovery_interval_seconds)
            await self._run_supervised(
                "symbol_discovery", lambda: asyncio.to_thread(self._refresh_symbol_universe)
            )

    async def _tournament_loop(self) -> None:
        """Runs on its own cadence, independent of everything else.
        Observation-only: builds up StrategyManager.winner_history (and the
        journal's tournament_events table) so stability across repeated
        retraining cycles can be reviewed later. Never reads from this
        loop's results to influence a live decision."""
        while True:
            await asyncio.sleep(self.tournament_interval_seconds)
            await self._run_supervised("tournament", self._run_tournament_all_symbols)

    async def _retention_loop(self) -> None:
        """Runs on its own (typically daily) cadence, independent of
        everything else. Deletes rows older than retention_days from the
        high-frequency/operational tables only - trades, trade_audit,
        alerts, retrain_events, and circuit_breaker_events are never
        touched, regardless of age. See Journal._PRUNABLE_TABLES."""
        while True:
            await asyncio.sleep(self.retention_interval_seconds)
            # Offloaded via to_thread like _symbol_discovery_loop, unlike
            # the OTHER two loops (retrain/tournament) whose bodies are
            # already async - _prune_old_records is a plain sync DB call
            # (DELETE across every prunable table); without to_thread it
            # ran directly on the event loop, blocking cycle scheduling and
            # every other background loop's timer for its duration.
            await self._run_supervised("retention", lambda: asyncio.to_thread(self._prune_old_records))

    def _prune_old_records(self) -> None:
        deleted = self.journal.prune_old_records(self.retention_days)
        total = sum(deleted.values())
        if total:
            logger.info(f"retention pruning deleted {total} old rows: {deleted}")

    async def _position_monitor_loop(self) -> None:
        """Runs on a much faster cadence than the main decision cycle
        (poll_interval_seconds, 5 min by default) - trailing stop,
        partial profit, and trend exit all react to real intrabar price
        moves, not just once every full cycle. Real exchange-side
        protective stops already react instantly regardless of this
        loop's cadence (they're resting orders Binance itself fills) -
        this closes the gap for everything only checked FROM this
        process. Touches ONLY symbols with an open position, via a cheap
        ticker fetch each - never generates a new BUY signal, never
        retrains anything, never re-runs symbol discovery."""
        while True:
            await asyncio.sleep(self.position_monitor_interval_seconds)
            await self._run_supervised("position_monitor", lambda: asyncio.to_thread(self._monitor_open_positions))

    def _monitor_open_positions(self) -> None:
        if self.execution.exchange is None:
            return
        open_symbols = list(self.portfolio.portfolio.positions.keys())
        for symbol in open_symbols:
            if self._shutdown_event.is_set():
                return
            try:
                ticker = self.execution.exchange.fetch_ticker(symbol)
                current_price = float(ticker["last"])
            except Exception:  # noqa: BLE001 - one symbol's ticker failure must not skip the rest
                logger.exception("position monitor: could not fetch ticker", extra={"context": {"symbol": symbol}})
                continue

            if self._reconcile_triggered_stop(symbol, current_price):
                continue
            self._update_trailing_stop(symbol, current_price)
            self._maybe_take_partial_profit(symbol, current_price)
            # Reuses the main cycle's own last-fetched OHLCV frame (cached
            # in run_cycle) for the SMA - a 1h-timeframe indicator doesn't
            # meaningfully change within this loop's ~15-30s cadence, so
            # recomputing it from a fresh candle fetch every tick would
            # cost an extra API call for no real signal benefit. The PRICE
            # side of the check is still fully real-time (the ticker
            # fetched just above).
            snapshot = self._last_snapshots.get(symbol)
            close_series = snapshot.frame["close"] if snapshot is not None else None
            self._maybe_trend_exit(symbol, current_price, close_series)

    async def _run_tournament_all_symbols(self) -> None:
        for symbol in self.market_monitor.symbols:
            if self._shutdown_event.is_set():
                # A full pass can take 15-25 minutes across a large
                # universe (see config.yaml's own comments) - cancelling
                # the asyncio.Task wrapping this loop does NOT stop
                # already-running work inside asyncio.to_thread below (the
                # underlying thread keeps running to completion
                # regardless), so the only way to actually shorten a
                # shutdown's worst case is to check BETWEEN symbols and
                # stop starting new ones, rather than only at the top of
                # the loop's own while-True in _tournament_loop.
                logger.info("tournament pass aborted early: shutdown requested")
                return
            try:
                await asyncio.to_thread(
                    self.strategy_manager.run_tournament_from_live, symbol, self.market_monitor.timeframe
                )
            except Exception:  # noqa: BLE001 - a failed tournament run must not kill the live loop
                logger.exception("tournament run failed", extra={"context": {"symbol": symbol}})
                continue

            if self.champion_challenger is not None:
                # Same cadence as the tournament itself - a natural
                # checkpoint to ask "of everything paper-trading right now,
                # who's beating the real champion and who's dead weight."
                # Never gates or retires anything automatically; only
                # surfaces a signal for an operator to review (see both
                # methods' docstrings).
                try:
                    await asyncio.to_thread(
                        self.champion_challenger.compare_to_champion, symbol, self.portfolio.portfolio
                    )
                    await asyncio.to_thread(
                        self.champion_challenger.underperforming_candidates, symbol, self.portfolio.portfolio
                    )
                except Exception:  # noqa: BLE001 - a failed comparison must not kill the live loop
                    logger.exception("challenger comparison failed", extra={"context": {"symbol": symbol}})

    def _refresh_symbol_universe(self) -> None:
        """Re-select the watchlist from SymbolDiscoveryAgent, merged with
        any symbol currently holding an open position.

        A symbol dropping out of the top-N by volume must never mean losing
        track of a position already held in it - that position still needs
        risk management and an exit, discovery ranking or not.
        """
        try:
            discovered = self.symbol_discovery.discover()
        except Exception:  # noqa: BLE001 - a failed discovery must not kill the live loop
            logger.exception("symbol discovery failed")
            return

        held_symbols = list(self.portfolio.portfolio.positions.keys())
        combined = list(dict.fromkeys(discovered + held_symbols))  # dedupe, preserve order

        added = set(combined) - set(self.market_monitor.symbols)
        removed = set(self.market_monitor.symbols) - set(combined)
        self.market_monitor.update_symbols(combined)

        if added or removed:
            logger.info(
                "symbol universe updated",
                extra={
                    "context": {
                        "added": sorted(added),
                        "removed": sorted(removed),
                        "total": len(combined),
                    }
                },
            )

    async def _retrain_all_symbols(self) -> None:
        # Two phases, not one combined per-symbol retrain_from_live call
        # per symbol: fetching data stays isolated per-symbol (one
        # symbol's feed failure must not lose the rest), but the actual
        # promotion DECISION for every successfully-fetched symbol is
        # made together in one batched call, so LearningAgent.retrain_all
        # can correct significance across the whole retrain cycle's
        # family of tests (Benjamini-Hochberg - see that method and
        # trading_assistant.validation.significance's module docstring on
        # why a fixed per-test alpha, applied independently to each
        # symbol on this recurring cadence, eventually promotes noise).
        symbols_and_ohlcv = []
        for symbol in self.market_monitor.symbols:
            if self._shutdown_event.is_set():
                # See _run_tournament_all_symbols' identical guard for why
                # this needs to be checked between symbols, not just at
                # the top of _retrain_loop's while-True.
                logger.info("retrain pass aborted early: shutdown requested")
                return
            try:
                # Offload to a worker thread: fetching real OHLCV history
                # is blocking network I/O that would otherwise freeze the
                # live run_cycle loop (including reacting to a pending
                # SELL). Must fetch on the same timeframe MarketMonitorAgent
                # polls live - otherwise a promoted model gets applied to a
                # bar size/distribution it was never validated against.
                ohlcv = await asyncio.to_thread(
                    self.learning.fetch_ohlcv_for_retrain, symbol, timeframe=self.market_monitor.timeframe
                )
                symbols_and_ohlcv.append((symbol, ohlcv))
            except Exception:  # noqa: BLE001 - a failed fetch must not kill the rest of the retrain pass
                logger.exception("could not fetch retrain data", extra={"context": {"symbol": symbol}})

        if not symbols_and_ohlcv:
            return
        try:
            # Offload to a worker thread: trains real models (RandomForest
            # + XGBoost) plus a backtest for every symbol in the batch -
            # all blocking, CPU-bound work.
            await asyncio.to_thread(self.learning.retrain_all, symbols_and_ohlcv)
        except Exception:  # noqa: BLE001 - a failed retrain batch must not kill the live loop
            logger.exception("retrain batch failed", extra={"context": {"symbols": [s for s, _ in symbols_and_ohlcv]}})