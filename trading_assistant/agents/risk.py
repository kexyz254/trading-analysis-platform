"""RiskAgent: gates a proposed trade through position sizing, daily-loss, and portfolio limit checks."""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Deque, Dict, List, Optional

import pandas as pd

from trading_assistant.agents.alerting import AlertManager
from trading_assistant.agents.circuit_breaker import CircuitBreaker
from trading_assistant.agents.features import build_features
from trading_assistant.persistence.journal import Journal
from trading_assistant.portfolio.limits import check_portfolio_limits
from trading_assistant.portfolio.manager import RiskManager, wilson_lower_bound
from trading_assistant.risk.adaptive import DrawdownTier, compute_drawdown_pct, compute_risk_scale
from trading_assistant.risk.correlation import compute_returns, correlated_exposure_pct
from trading_assistant.risk.crisis import CrisisState
from trading_assistant.risk.timeframe_confluence import higher_timeframe_blocks_buy, higher_timeframe_regime
from trading_assistant.risk.atr_stop import DEFAULT_MAX_STOP_PCT, DEFAULT_MIN_STOP_PCT, compute_atr_stop_pct
from trading_assistant.risk.trend_alignment import (
    adx_blocks_buy,
    adx_confirms_trend,
    compute_sma_slope_rising,
    compute_trend_alignment,
    sma_slope_blocks_buy,
    trend_alignment_blocks_buy,
    volume_blocks_buy,
    volume_confirms_breakout,
)

logger = logging.getLogger(__name__)


def latest_feature_row(ohlcv_frame: Optional[pd.DataFrame]) -> Optional[pd.Series]:
    """The most recent bar's full engineered feature row from raw OHLCV -
    computed via the same build_features every other consumer uses, so
    every derived check (ATR-based stop distance, ADX trend strength,
    relative volume) reads from ONE indicator computation per evaluate_buy
    call, not a separately re-run one per check. Lives here (not in a
    risk.* submodule, which stays free of an agents.features dependency)
    to avoid a circular import: agents.__init__ eagerly imports
    Orchestrator -> RiskAgent, so a risk.* submodule importing
    agents.features would create risk -> agents -> risk."""
    if ohlcv_frame is None or ohlcv_frame.empty:
        return None
    featured = build_features(ohlcv_frame)
    if featured.empty:
        return None
    return featured.iloc[-1]


def _feature_value(row: Optional[pd.Series], column: str) -> Optional[float]:
    if row is None or column not in row:
        return None
    value = row[column]
    return None if pd.isna(value) else float(value)


def latest_atr_pct(ohlcv_frame: Optional[pd.DataFrame]) -> Optional[float]:
    """The most recent bar's atr_pct (ATR as a fraction of price)."""
    return _feature_value(latest_feature_row(ohlcv_frame), "atr_pct")


@dataclass
class RiskVerdict:
    approved: bool
    reason: Optional[str]
    quantity: float = 0.0  # notional size in quote currency; caller divides by price for asset quantity
    # The ACTUAL per-trade stop distance used to size this approval - None
    # when volatility-adjusted sizing is disabled (the caller should fall
    # back to its own static default). ExecutionAgent must place the real
    # protective stop at THIS distance, not a static one, or the stop
    # placed on the exchange would silently disagree with what position
    # sizing above already assumed.
    stop_loss_pct: Optional[float] = None


class RiskAgent:
    """The single gate every proposed trade must pass before execution.

    Combines RiskManager's position sizing / daily-loss check (now with the
    reset bug fixed, see portfolio.manager.RiskManager.reset_if_new_day)
    with portfolio-level exposure/concentration limits from portfolio.limits.
    """

    def __init__(
        self,
        risk_manager: Optional[RiskManager] = None,
        max_position_size: float = 0.1,
        max_daily_loss: float = 0.02,
        risk_per_trade: float = 0.02,
        stop_loss_pct: float = 0.01,
        max_position_weight: Optional[float] = 0.25,
        # Ratios of LIVE capital (evaluate_buy's own `capital` param),
        # converted to the absolute quote-currency caps portfolio.limits.
        # check_portfolio_limits actually checks against, freshly computed
        # every call - a fixed dollar cap tied to some earlier capital
        # figure would silently stop tracking reality the moment real
        # capital diverged from it (too loose once capital grew past it,
        # too tight if it never got updated after a withdrawal). None
        # disables the respective check entirely.
        max_gross_exposure_pct: Optional[float] = None,
        max_net_exposure_pct: Optional[float] = None,
        circuit_breaker: Optional[CircuitBreaker] = None,
        max_consecutive_errors: int = 3,
        journal: Optional[Journal] = None,
        # Per-symbol caps alone can't see that many "different" symbols may
        # really be one leveraged bet on the same market factor - crypto
        # altcoins are typically 70-90%+ correlated to BTC, especially
        # during risk-off moves. None disables this check (opt-in, backward
        # compatible - existing callers that never pass price_history to
        # evaluate_buy get exactly the old behavior).
        max_correlated_exposure_pct: Optional[float] = None,
        correlation_threshold: float = 0.6,
        correlation_lookback_bars: int = 50,
        # A fixed risk_per_trade risks the same dollar fraction the first
        # trade after a fresh equity peak and the fiftieth trade during a
        # 15% drawdown. None disables adaptive scaling (opt-in via
        # update_equity actually being called - see below); pass
        # DEFAULT_DRAWDOWN_TIERS (or a custom ladder) to enable it.
        drawdown_tiers: Optional[List[DrawdownTier]] = None,
        # Top-down analysis discipline: a 1h BUY against a confirmed higher-
        # timeframe downtrend is the classic retracement trap - buying a
        # pullback on a lower timeframe without checking the higher
        # timeframe hasn't actually reversed. None disables this check
        # (opt-in, backward compatible - existing callers that never pass
        # ohlcv_frame to evaluate_buy get exactly the old behavior). A
        # factor of 4 against 1h bars means a 4h higher timeframe.
        higher_timeframe_factor: Optional[int] = None,
        alert_manager: Optional[AlertManager] = None,
        # A HARD stop, distinct from drawdown_tiers' soft auto-scaling
        # (which floors NEW-position risk to zero at its deepest tier but
        # auto-recovers as equity recovers, with no human ever necessarily
        # noticing). Past this threshold the circuit breaker TRIPS -
        # halting new positions until an operator deliberately reviews and
        # resets, the same deliberate-review bar max_daily_loss already
        # gates. None disables it (opt-in, backward compatible).
        max_drawdown_pct: Optional[float] = None,
        # Volatility-adjusted stop distance (trading_assistant.risk.atr_stop):
        # None keeps stop_loss_pct fixed for every symbol (opt-in, backward
        # compatible). A number (e.g. 2.0) makes the actual per-trade stop
        # distance atr_multiplier times that symbol's own recent ATR,
        # clamped to [min_atr_stop_pct, max_atr_stop_pct] - both the
        # protective stop AND the position size derived from it become
        # volatility-aware together, since size is computed FROM the stop
        # distance.
        atr_multiplier: Optional[float] = None,
        min_atr_stop_pct: float = DEFAULT_MIN_STOP_PCT,
        max_atr_stop_pct: float = DEFAULT_MAX_STOP_PCT,
        # Confidence-based sizing via the Kelly criterion: once enough REAL
        # trade history exists, cap position size at what this system's
        # own actual win rate/payoff ratio statistically justifies, rather
        # than sizing every qualifying trade the same regardless of
        # whether recent performance actually supports betting that much.
        # None (default) disables it - opt-in, backward compatible.
        # Deliberately a CAP on top of risk-based sizing, never a
        # loosening of it: Kelly can only shrink a position, not grow one
        # beyond what risk_per_trade/stop distance already sized.
        kelly_min_trades: Optional[int] = None,
        kelly_lookback_trades: int = 30,
        # Crisis correlation override (trading_assistant.risk.crisis): a
        # blunt safety overlay on top of max_correlated_exposure_pct above
        # - rolling Pearson correlation is estimated from calm-period data
        # and understates how correlated everything becomes in a genuine
        # panic. None disables it entirely (opt-in, backward compatible -
        # existing callers that never call update_crisis_state get exactly
        # the old behavior). A number (e.g. 0.15) means: if crisis_symbol
        # drops that much within crisis_window, treat EVERY held position
        # as if perfectly correlated with any new candidate for
        # crisis_duration (or until crisis_symbol recovers, whichever
        # comes first) - a pure hard cap, not a replacement for the
        # existing correlation model.
        crisis_drop_threshold: Optional[float] = None,
        crisis_symbol: str = "BTC/USDT",
        crisis_window_hours: float = 1.0,
        crisis_duration_hours: float = 24.0,
        # See RiskConfig.min_stake_usd's docstring for the full reasoning -
        # a pure risk_per_trade%/stop-distance calculation produces a
        # position too small for a real exchange to accept once capital is
        # small. Below this floor (or the symbol's real exchange minimum,
        # whichever is larger - see evaluate_buy's exchange_min_notional
        # param), size is bumped up rather than silently producing a
        # doomed order, but only when capital can comfortably afford it.
        min_stake_usd: float = 1.0,
        # SMA trend-alignment confluence (trading_assistant.risk.
        # trend_alignment): only take a new long while price > SMA_fast >
        # SMA_slow on the SAME timeframe this decision is being made on -
        # distinct from higher_timeframe_factor above (a DIFFERENT,
        # higher timeframe's regime) and from SMACrossoverStrategy (one
        # tournament candidate's own signal, not a gate applied to every
        # buy). None disables this check entirely (opt-in, backward
        # compatible - existing callers that never pass ohlcv_frame, or
        # that leave this None, get exactly the old behavior).
        trend_alignment_fast_period: Optional[int] = None,
        trend_alignment_slow_period: int = 50,
        # Require the fast SMA to itself be sloping upward, not just sit
        # above the slow SMA - see trend_alignment.compute_sma_slope_rising's
        # docstring. Only applies when trend_alignment_fast_period is also
        # set (opt-in on top of an opt-in check).
        require_sma_slope_rising: bool = False,
        # ADX trend-strength confirmation (0-100 scale, the standard
        # technical-analysis convention - 25 is the common "genuinely
        # trending, not just drifting" bar). None disables entirely
        # (opt-in, backward compatible).
        adx_threshold: Optional[float] = None,
        # Volume confirmation: current bar's volume must be at least this
        # multiple of its own rolling average (1.0 = at least the recent
        # average) to confirm real participation behind a move. None
        # disables entirely (opt-in, backward compatible).
        volume_confirmation_threshold: Optional[float] = None,
    ):
        self.trend_alignment_fast_period = trend_alignment_fast_period
        self.trend_alignment_slow_period = trend_alignment_slow_period
        self.require_sma_slope_rising = require_sma_slope_rising
        self.adx_threshold = adx_threshold
        self.volume_confirmation_threshold = volume_confirmation_threshold
        self.risk_manager = risk_manager or RiskManager(
            max_position_size=max_position_size, max_daily_loss=max_daily_loss
        )
        self.risk_per_trade = risk_per_trade
        self.stop_loss_pct = stop_loss_pct
        self.atr_multiplier = atr_multiplier
        self.min_atr_stop_pct = min_atr_stop_pct
        self.max_atr_stop_pct = max_atr_stop_pct
        self.kelly_min_trades = kelly_min_trades
        self._trade_pnl_fractions: Deque[float] = deque(maxlen=kelly_lookback_trades)
        self.max_position_weight = max_position_weight
        self.max_gross_exposure_pct = max_gross_exposure_pct
        self.max_net_exposure_pct = max_net_exposure_pct
        self.max_correlated_exposure_pct = max_correlated_exposure_pct
        self.correlation_threshold = correlation_threshold
        self.correlation_lookback_bars = correlation_lookback_bars
        self.drawdown_tiers = drawdown_tiers
        self.higher_timeframe_factor = higher_timeframe_factor
        # Peak equity is only ever advanced by update_equity() - never
        # touched by evaluate_buy/evaluate_sell - so drawdown reflects real
        # mark-to-market equity from the orchestrator's own cycle, not a
        # value reconstructed ad hoc from whatever positions_df happens to
        # look like at the moment one specific symbol's BUY is evaluated.
        self._peak_equity: Optional[float] = None
        self._current_risk_scale: float = 1.0
        self.max_drawdown_pct = max_drawdown_pct
        self.circuit_breaker = circuit_breaker or CircuitBreaker(
            max_consecutive_errors=max_consecutive_errors, journal=journal, alert_manager=alert_manager
        )
        self.crisis_drop_threshold = crisis_drop_threshold
        self.crisis_symbol = crisis_symbol
        self.crisis_window = timedelta(hours=crisis_window_hours)
        self.crisis_duration = timedelta(hours=crisis_duration_hours)
        self._crisis_state = CrisisState()
        self.min_stake_usd = min_stake_usd

    def update_equity(self, current_equity: float) -> None:
        """Feed the orchestrator's real, current mark-to-market equity in
        every cycle (not just when a BUY is being evaluated) so peak
        tracking - and therefore the drawdown-based risk scale applied to
        the NEXT evaluate_buy call - stays current even on cycles where no
        symbol happens to signal a trade."""
        if current_equity <= 0:
            return
        self._peak_equity = max(self._peak_equity or current_equity, current_equity)
        if self.drawdown_tiers is None and self.max_drawdown_pct is None:
            return
        drawdown_pct = compute_drawdown_pct(self._peak_equity, current_equity)
        if self.drawdown_tiers is not None:
            self._current_risk_scale = compute_risk_scale(drawdown_pct, self.drawdown_tiers)
        if (
            self.max_drawdown_pct is not None
            and drawdown_pct >= self.max_drawdown_pct
            and not self.circuit_breaker.tripped
        ):
            self.circuit_breaker.trip(
                f"max drawdown breached: {drawdown_pct:.1%} >= {self.max_drawdown_pct:.1%} "
                f"(peak equity {self._peak_equity:.2f}, current {current_equity:.2f})"
            )

    def update_crisis_state(self, crisis_symbol_frame: Optional[pd.DataFrame], now: Optional[datetime] = None) -> None:
        """Feed the orchestrator's freshly-fetched OHLCV frame for
        crisis_symbol every cycle (not just when a BUY is being evaluated)
        so the crisis window is checked against the LATEST bar every
        cycle, and so a triggered override's 24h/recovery clearing (via
        is_active, checked inside evaluate_buy) reflects real elapsed time
        even on cycles where no symbol happens to signal a trade - same
        reasoning as update_equity above. No-op if crisis_drop_threshold
        is None (opt-in, backward compatible)."""
        if self.crisis_drop_threshold is None:
            return
        self._crisis_state.update(crisis_symbol_frame, self.crisis_drop_threshold, self.crisis_window, now=now)

    def evaluate_buy(
        self,
        symbol: str,
        price: float,
        capital: float,
        positions_df: pd.DataFrame,
        price_history: Optional[Dict[str, pd.Series]] = None,
        ohlcv_frame: Optional[pd.DataFrame] = None,
        # The real exchange's own minimum order notional for this specific
        # symbol (e.g. Binance's MIN_NOTIONAL filter - $5 for most pairs,
        # occasionally lower). None when unavailable (market data not
        # loaded, testnet/paper context) - falls back to just
        # self.min_stake_usd in that case. Passed in rather than looked up
        # here because RiskAgent has no exchange handle of its own - the
        # caller (Orchestrator, which owns execution.exchange) is
        # responsible for the lookup.
        exchange_min_notional: Optional[float] = None,
    ) -> RiskVerdict:
        if self.circuit_breaker.tripped:
            return RiskVerdict(approved=False, reason=f"circuit breaker tripped: {self.circuit_breaker.trip_reason}")

        self.risk_manager.reset_if_new_day()
        if not self.risk_manager.check_daily_loss_limit():
            self.circuit_breaker.trip("daily loss limit breached")
            return RiskVerdict(approved=False, reason="daily loss limit already breached")

        if capital <= 0:
            return RiskVerdict(approved=False, reason="no capital available")

        if self.drawdown_tiers is not None and self._current_risk_scale <= 0.0:
            return RiskVerdict(
                approved=False,
                reason=f"drawdown circuit halt: equity is too far below its peak (${self._peak_equity:,.2f}) to open new positions",
            )

        if self.higher_timeframe_factor is not None and ohlcv_frame is not None:
            higher_tf_regime = higher_timeframe_regime(ohlcv_frame, factor=self.higher_timeframe_factor)
            if higher_timeframe_blocks_buy(higher_tf_regime):
                return RiskVerdict(
                    approved=False,
                    reason=(
                        f"higher-timeframe confluence: {symbol} would be a buy against a confirmed "
                        f"{higher_tf_regime} on the higher timeframe (top-down analysis discipline)"
                    ),
                )

        # Computed once (if ohlcv_frame is available) and reused by every
        # feature-derived check below (ADX, volume, ATR stop distance) -
        # build_features runs the full indicator suite, so this avoids
        # re-running it once per check for the same symbol/cycle.
        feature_row = latest_feature_row(ohlcv_frame) if ohlcv_frame is not None else None

        if self.trend_alignment_fast_period is not None and ohlcv_frame is not None:
            alignment = compute_trend_alignment(
                ohlcv_frame["close"], self.trend_alignment_fast_period, self.trend_alignment_slow_period
            )
            if trend_alignment_blocks_buy(alignment):
                severity = (
                    "price has fallen below BOTH the fast and slow SMA - be more cautious, this is a "
                    "stronger bearish signal"
                    if alignment == "below_slow"
                    else "price has fallen below the fast SMA - avoid new longs until it reclaims it"
                )
                return RiskVerdict(
                    approved=False,
                    reason=(
                        f"trend alignment: {symbol} is not in a price > SMA{self.trend_alignment_fast_period} > "
                        f"SMA{self.trend_alignment_slow_period} uptrend structure ({severity})"
                    ),
                )

            if self.require_sma_slope_rising:
                is_rising = compute_sma_slope_rising(ohlcv_frame["close"], self.trend_alignment_fast_period)
                if sma_slope_blocks_buy(is_rising):
                    return RiskVerdict(
                        approved=False,
                        reason=(
                            f"trend alignment: {symbol}'s SMA{self.trend_alignment_fast_period} is above "
                            f"SMA{self.trend_alignment_slow_period} but not itself sloping upward - too "
                            "weak a claim to a real uptrend yet"
                        ),
                    )

        if self.adx_threshold is not None:
            adx_value = _feature_value(feature_row, "adx_strength")
            adx_value_pct = adx_value * 100.0 if adx_value is not None else None
            if adx_blocks_buy(adx_confirms_trend(adx_value_pct, self.adx_threshold)):
                return RiskVerdict(
                    approved=False,
                    reason=(
                        f"ADX confirmation: {symbol}'s ADX ({adx_value_pct:.1f}) is below the "
                        f"{self.adx_threshold:.0f} trend-strength threshold - too weak/ranging a trend to buy into"
                    ),
                )

        if self.volume_confirmation_threshold is not None:
            relative_volume = _feature_value(feature_row, "relative_volume")
            if volume_blocks_buy(volume_confirms_breakout(relative_volume, self.volume_confirmation_threshold)):
                return RiskVerdict(
                    approved=False,
                    reason=(
                        f"volume confirmation: {symbol}'s current volume ({relative_volume:.2f}x its own "
                        f"20-bar average) is below the {self.volume_confirmation_threshold:.1f}x threshold - "
                        "not enough real participation behind this move"
                    ),
                )

        # A stop distance calibrated to THIS symbol's own recent volatility,
        # not one fixed value borrowed across every symbol regardless of
        # how much it typically moves - see volatility_sizing's docstring.
        # Falls back to the static stop_loss_pct when disabled (opt-in) or
        # when ohlcv_frame/ATR isn't available (a data gap, not grounds to
        # block the trade).
        effective_stop_pct = self.stop_loss_pct
        if self.atr_multiplier is not None and ohlcv_frame is not None:
            effective_stop_pct = compute_atr_stop_pct(
                _feature_value(feature_row, "atr_pct"),
                fallback_stop_pct=self.stop_loss_pct,
                atr_multiplier=self.atr_multiplier,
                min_stop_pct=self.min_atr_stop_pct,
                max_stop_pct=self.max_atr_stop_pct,
            )

        # Scale the FINAL computed size, not just the risk_per_trade input:
        # calculate_position_size caps at capital*max_position_size, which
        # under this system's own default config (risk_per_trade=0.02,
        # stop_loss_pct=0.01 -> an uncapped size of 2x capital) is ALWAYS
        # the binding constraint, not risk_per_trade. Scaling only
        # risk_per_trade would have made the drawdown ladder have zero
        # real effect on position size - caught by the test for this
        # feature actually asserting on the resulting quantity, not just
        # that update_equity() runs without error.
        quantity_notional = self.risk_manager.calculate_position_size(
            capital, risk_per_trade=self.risk_per_trade, stop_loss_pct=effective_stop_pct
        )
        quantity_notional *= self._current_risk_scale

        # Confidence via REAL track record, not just the model's own
        # probability output: once enough live trades exist, cap size at
        # what this system's actual win rate/payoff ratio justifies (see
        # _kelly_position_scale) - a CAP only, never a reason to grow a
        # position beyond what risk-based sizing above already computed.
        kelly_scale = self._kelly_position_scale()
        if kelly_scale < 1.0:
            quantity_notional = min(quantity_notional, capital * kelly_scale)

        if quantity_notional <= 0:
            return RiskVerdict(approved=False, reason="calculated position size is zero")

        # Small-account floor: see RiskConfig.min_stake_usd's docstring for
        # the full reasoning. A pure risk_per_trade%/Kelly/drawdown-scaled
        # size is often far too small for a real exchange to ever accept
        # once capital is small - rather than let that produce a doomed
        # order (wasting circuit-breaker error budget on a rejection that
        # was mathematically certain), bump up to the floor when capital
        # can comfortably afford it, or refuse cleanly with a clear reason
        # when it can't. Once capital grows enough that risk-based sizing
        # naturally clears the floor on its own, this never triggers again
        # - sizing "adapts" back to pure risk_per_trade% with no further
        # intervention, exactly the professional-vs-toy distinction this
        # exists for.
        # Real incident: a $5.00-exact BTC/USDT buy filled correctly, but
        # Binance's ~0.1% trading fee (taken out of the BTC received) plus
        # rounding the remainder DOWN to the exchange's lot-size step
        # together shaved the actually-held balance to $4.48 - below the
        # $5 minimum for BOTH placing a protective stop and even a full
        # exit sell, leaving a real position that could not be protected
        # OR closed until price moved back in its favor. The effect is
        # worst for high-priced assets (BTC's lot step alone is worth
        # >10% of a bare $5 order) - a flat 15% buffer comfortably
        # survives fee erosion plus one lot-step's worth of rounding loss
        # for every symbol actually seen live this session.
        _EXCHANGE_MIN_NOTIONAL_SAFETY_MARGIN = 1.15
        stake_floor = max(
            self.min_stake_usd,
            (exchange_min_notional or 0.0) * _EXCHANGE_MIN_NOTIONAL_SAFETY_MARGIN,
        )
        if quantity_notional < stake_floor:
            # 5% margin so the order clears comfortably even after
            # commission/slippage, rather than sizing to the exact edge of
            # what capital allows and risking an exchange-side rejection
            # for insufficient balance.
            if stake_floor <= capital * 0.95:
                quantity_notional = stake_floor
            else:
                return RiskVerdict(
                    approved=False,
                    reason=(
                        f"capital (${capital:.2f}) is too small to comfortably meet the ${stake_floor:.2f} "
                        f"minimum stake for {symbol}"
                    ),
                )

        if price > 0:
            # Check against the portfolio AS IT WOULD BE if this trade were
            # approved, not just already-held exposure - otherwise the gate
            # can only ever catch a breach one cycle late, and previously
            # skipped the check entirely on an empty portfolio (e.g. the
            # very first trade).
            #
            # max_position_weight is concentration relative to the *existing*
            # book (position value / total position value) - for a symbol's
            # very first position that's trivially ~100% no matter how small
            # the trade is, so it's only a meaningful check once there's
            # something to be concentrated relative to. max_gross_exposure_pct/
            # max_net_exposure_pct (a ratio of live capital) remain meaningful
            # even for a first trade.
            position_weight_limit = self.max_position_weight if not positions_df.empty else None
            # Computed fresh from THIS call's live capital, not cached at
            # construction time - the whole point of switching from a fixed
            # dollar cap to a ratio is that it tracks capital as it changes.
            gross_exposure_limit = (
                self.max_gross_exposure_pct * capital if self.max_gross_exposure_pct is not None else None
            )
            net_exposure_limit = (
                self.max_net_exposure_pct * capital if self.max_net_exposure_pct is not None else None
            )
            active_limits = (position_weight_limit, gross_exposure_limit, net_exposure_limit)
            if any(limit is not None for limit in active_limits):
                projected_positions = self._project_positions(
                    symbol, price, quantity_notional / price, positions_df
                )
                report = check_portfolio_limits(
                    projected_positions,
                    max_position_weight=position_weight_limit,
                    max_gross_exposure=gross_exposure_limit,
                    max_net_exposure=net_exposure_limit,
                )
                if not report.is_within_limits:
                    reasons = "; ".join(
                        f"{violation.limit_name} breached for {violation.symbol or 'portfolio'}"
                        for violation in report.violations
                    )
                    return RiskVerdict(approved=False, reason=reasons)

        if self.max_correlated_exposure_pct is not None and price_history is not None and not positions_df.empty:
            held_notional: Dict[str, float] = {
                row.symbol: float(row.quantity) * float(row.price) for row in positions_df.itertuples()
            }
            total_portfolio_value = capital + sum(held_notional.values())

            crisis_symbol_series = price_history.get(self.crisis_symbol)
            crisis_current_price = (
                float(crisis_symbol_series.iloc[-1])
                if crisis_symbol_series is not None and len(crisis_symbol_series) else None
            )
            crisis_active = self.crisis_drop_threshold is not None and self._crisis_state.is_active(
                crisis_current_price, self.crisis_drop_threshold, self.crisis_duration
            )

            if crisis_active:
                # Treat every held position as if perfectly correlated with
                # the candidate - the rolling Pearson estimate below is
                # exactly what a genuine panic proves wrong, generally
                # after the fact. Makes max_correlated_exposure_pct act as
                # a blunt total-gross-exposure cap for the override's
                # duration - see trading_assistant.risk.crisis.
                pct = (
                    (quantity_notional + sum(held_notional.values())) / total_portfolio_value
                    if total_portfolio_value > 0 else 0.0
                )
            else:
                returns = compute_returns(price_history, lookback_bars=self.correlation_lookback_bars)
                pct = correlated_exposure_pct(
                    candidate_symbol=symbol,
                    candidate_notional=quantity_notional,
                    held_notional=held_notional,
                    returns=returns,
                    total_portfolio_value=total_portfolio_value,
                    correlation_threshold=self.correlation_threshold,
                )
            if pct > self.max_correlated_exposure_pct:
                crisis_note = (
                    "crisis correlation override active - treating all positions as correlated"
                    if crisis_active else f"symbols correlated >= {self.correlation_threshold:.0%} with {symbol}"
                )
                return RiskVerdict(
                    approved=False,
                    reason=(
                        f"correlated-cluster exposure {pct:.0%} would exceed "
                        f"{self.max_correlated_exposure_pct:.0%} cap ({crisis_note})"
                    ),
                )

        return RiskVerdict(approved=True, reason=None, quantity=quantity_notional, stop_loss_pct=effective_stop_pct)

    @staticmethod
    def _project_positions(symbol: str, price: float, quantity: float, positions_df: pd.DataFrame) -> pd.DataFrame:
        """positions_df with the proposed trade appended, for a forward-looking limit check."""
        proposed_row = pd.DataFrame([{"symbol": symbol, "quantity": quantity, "price": price}])
        if positions_df.empty:
            return proposed_row
        return pd.concat([positions_df, proposed_row], ignore_index=True)

    def evaluate_sell(self, symbol: str) -> RiskVerdict:
        # Flattening an existing position always reduces risk, so it's never blocked.
        return RiskVerdict(approved=True, reason=None)

    def record_realized_pnl(self, pnl: float, capital: float) -> None:
        """Feed a closed trade's realized PnL into the daily-loss counter,
        and (when kelly_min_trades is configured) into the rolling trade
        history the Kelly-based sizing cap is computed from."""
        if capital <= 0:
            return
        fraction = pnl / capital
        self.risk_manager.record_trade_pnl(fraction)
        if self.kelly_min_trades is not None:
            self._trade_pnl_fractions.append(fraction)

    def _kelly_position_scale(self) -> float:
        """The fraction of capital this system's own REAL trade history
        statistically justifies risking on a single trade, via
        RiskManager.calculate_kelly_fraction - previously written but
        never called anywhere. Returns 1.0 (no cap at all) when disabled,
        or when there isn't yet enough real trade history to trust the
        estimate - a small, noisy sample producing a falsely confident
        Kelly number would be worse than not using one yet."""
        if self.kelly_min_trades is None or len(self._trade_pnl_fractions) < self.kelly_min_trades:
            return 1.0
        wins = [f for f in self._trade_pnl_fractions if f > 0]
        losses = [f for f in self._trade_pnl_fractions if f < 0]
        if not wins or not losses:
            return 1.0  # an all-winning or all-losing sample has no meaningful win/loss ratio to compute
        # Shrinkage: size Kelly off a conservative lower-confidence-bound
        # win rate (wilson_lower_bound), not the raw point estimate. Right
        # at kelly_min_trades this is meaningfully below the point
        # estimate (small sample, wide interval); it converges toward the
        # raw point estimate as more real trades accumulate past that
        # minimum (interval narrows) - smaller size early on, growing
        # toward the point-estimate Kelly as the track record grows,
        # instead of the previous hard on/off switch at kelly_min_trades
        # that trusted a right-at-the-minimum sample exactly as much as a
        # much larger one.
        win_rate = wilson_lower_bound(len(wins), len(self._trade_pnl_fractions))
        avg_win = sum(wins) / len(wins)
        avg_loss = sum(losses) / len(losses)
        return self.risk_manager.calculate_kelly_fraction(win_rate, avg_win, avg_loss)
