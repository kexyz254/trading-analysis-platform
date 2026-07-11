"""Configuration loading for the autonomous trading system.

Loads settings from a YAML file (default: ``config.yaml`` at the repo root)
and secrets from environment variables (populated via a ``.env`` file
through python-dotenv). The system defaults to Binance testnet/paper mode;
live trading requires an explicit ``mode: live`` in config plus real API
keys in the environment.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


@dataclass
class ExchangeConfig:
    name: str = "binance"
    api_key_env: str = "BINANCE_API_KEY"
    api_secret_env: str = "BINANCE_API_SECRET"
    # ccxt's own default (10s) is fine for an interactive script but not for
    # an unattended 24/7 loop - a stalled TCP connection isn't an exception,
    # it just blocks, and with no explicit timeout that can hang a live
    # cycle (or a background loop's iteration) indefinitely with nothing to
    # detect or recover from it. Applied to every exchange instance this
    # system constructs (run_system.py's build_exchange, LiveFeed).
    timeout_ms: int = 20000


@dataclass
class RiskConfig:
    max_position_size: float = 0.1
    max_daily_loss: float = 0.02
    risk_per_trade: float = 0.02
    stop_loss_pct: float = 0.01
    max_position_weight: float = 0.25
    # A pure risk_per_trade%/stop_loss_pct calculation produces a position
    # far too small for a real exchange to accept once capital is small
    # (e.g. $7.70 * 2% / 1% stop = $15.40 notional, then capped at
    # max_position_size*capital = $0.77 - Binance's own minimum order size
    # is $5+ for most pairs). Below this floor, the computed size is
    # bumped UP to whichever is larger of this and the symbol's real
    # exchange minimum (see RiskAgent.evaluate_buy's exchange_min_notional
    # param) - but only when capital can comfortably afford it; otherwise
    # the trade is cleanly refused rather than attempting an order that
    # would fail anyway. This is a DELIBERATE, AUDITABLE departure from
    # risk_per_trade for tiny accounts (there is no way to simultaneously
    # respect a 2% risk budget AND clear a real exchange minimum on a
    # single-digit-dollar account - professional micro-account trading
    # accepts a higher relative risk out of necessity, and this makes that
    # override visible rather than a silent violation). Once capital grows
    # enough that pure risk-based sizing naturally exceeds this floor, it
    # stops mattering at all - sizing "adapts" back to pure risk_per_trade%
    # with no further intervention.
    min_stake_usd: float = 1.0
    # Ratios of LIVE capital (RiskAgent.evaluate_buy already has `capital`
    # in scope - see its own docstring), not fixed dollar amounts. These
    # used to be absolute quote-currency caps tied to a fixed
    # PortfolioConfig.initial_capital, which meant they silently stopped
    # tracking reality the moment live capital diverged from that number
    # (too loose once real capital grew past it, too tight if it never
    # got updated) - self-adjusting instead, so a deposit or withdrawal
    # never requires revisiting this file. None disables the respective
    # check entirely.
    max_gross_exposure_pct: Optional[float] = 0.8
    max_net_exposure_pct: Optional[float] = 0.8
    max_consecutive_errors: int = 3
    # Correlation-aware concentration cap: per-symbol caps alone can't see
    # that many "different" symbols may really be one leveraged bet on the
    # same market factor - crypto altcoins are typically 70-90%+ correlated
    # to BTC, especially during risk-off moves. 0.4 means no single
    # correlated cluster (the candidate plus every held position moving
    # with it above correlation_threshold) may exceed 40% of total
    # portfolio value. None disables the check entirely.
    max_correlated_exposure_pct: Optional[float] = 0.4
    correlation_threshold: float = 0.6
    correlation_lookback_bars: int = 50
    # Drawdown-based adaptive risk scaling: risk shrinks in discrete steps as
    # equity falls further below its peak, floors at zero (halt opening new
    # positions) past the deepest tier - a losing streak earns a SMALLER
    # bet, not the same fixed one. Empty list disables adaptive scaling.
    drawdown_tiers: List[Dict[str, float]] = field(
        default_factory=lambda: [
            {"drawdown_pct": 0.05, "risk_scale": 0.75},
            {"drawdown_pct": 0.10, "risk_scale": 0.50},
            {"drawdown_pct": 0.15, "risk_scale": 0.25},
            {"drawdown_pct": 0.20, "risk_scale": 0.0},
        ]
    )
    # Top-down analysis discipline: a 1h BUY against a confirmed higher-
    # timeframe downtrend is the classic retracement trap - buying a
    # pullback on a lower timeframe without checking the higher timeframe
    # hasn't actually reversed. 4 (against 1h bars) means a 4h higher
    # timeframe, resampled from the same 1h history already fetched - zero
    # extra API calls. None disables the check entirely.
    higher_timeframe_factor: Optional[int] = 4
    # SMA trend-alignment confluence: only take a new long while
    # price > SMA_fast > SMA_slow on the SAME timeframe (20/50 by
    # default) - a same-timeframe structural check, distinct from
    # higher_timeframe_factor above (a DIFFERENT, higher timeframe's
    # regime). None disables the check entirely.
    trend_alignment_fast_period: Optional[int] = 20
    trend_alignment_slow_period: int = 50
    # Require the fast SMA to itself be sloping upward, not just sit above
    # the slow SMA - "aligned" alone can be true right after a sharp spike
    # that hasn't yet turned the average's own direction. Only applies
    # when trend_alignment_fast_period is also set.
    require_sma_slope_rising: bool = True
    # ADX trend-strength confirmation (0-100 scale, standard convention -
    # 25 is the common "genuinely trending, not just drifting" bar). None
    # disables the check entirely.
    adx_threshold: Optional[float] = 25.0
    # Volume confirmation: current bar's volume must be at least this
    # multiple of its own rolling average (1.0 = at least the recent
    # average) to confirm real participation behind a move. None disables
    # the check entirely.
    volume_confirmation_threshold: Optional[float] = 1.0
    # Pre-trade execution quality: a market order fired during a moment of
    # abnormally wide bid-ask spread can fill far worse than the price
    # risk-sizing assumed. 0.01 = reject a real buy if the current spread
    # exceeds 1% of mid price. None disables the check entirely.
    max_spread_pct: Optional[float] = 0.01
    # Adaptive order placement: a marketable LIMIT order (capped price)
    # instead of an unbounded MARKET order when the spread is comfortably
    # tight (at most 30% of max_spread_pct) - caps the worst-case fill
    # price without meaningfully risking a missed fill. False keeps every
    # real order a plain market order (backward compatible).
    enable_adaptive_order_type: bool = True
    # Trailing stop: once a position has moved favorably, its resting
    # protective stop follows price up (never down) instead of sitting
    # frozen at the original entry-based level for the position's whole
    # life. None disables it - a real position keeps its original fixed
    # stop_loss_pct-based stop forever, exactly as before this existed.
    trailing_stop_pct: Optional[float] = None
    # Trailing stop ACTIVATION threshold: the stop only starts trailing
    # once unrealized profit reaches this level (e.g. 0.02 = +2%) - below
    # it, the position keeps its original fixed stop untouched. None means
    # trailing is active from the moment a position opens.
    trailing_stop_activation_pct: Optional[float] = None
    # Trend exit: close a position entirely if price falls below its own
    # fast SMA (same period as trend_alignment_fast_period, by convention -
    # the entry structure this position was bought under has broken).
    # None disables it entirely.
    trend_exit_sma_period: Optional[int] = None
    # Partial profit-taking: once a position's unrealized gain crosses this
    # threshold, sell partial_profit_sell_fraction of it once (locking in
    # something concrete) and keep the remainder open, protected by
    # whatever stop it already has. None disables it entirely.
    partial_profit_pct: Optional[float] = None
    partial_profit_sell_fraction: float = 0.5
    # A HARD stop distinct from drawdown_tiers' soft auto-scaling: past
    # this drawdown, the circuit breaker TRIPS (halting all new positions
    # until an operator deliberately reviews and resets) rather than
    # silently auto-recovering as equity climbs back. None disables it.
    max_drawdown_pct: Optional[float] = None
    # Volatility-adjusted stop distance: a fixed stop_loss_pct applied to
    # every symbol regardless of its actual volatility either sits too
    # close (stopped out on ordinary noise for a volatile altcoin) or too
    # far (barely protects a near-stablecoin pair). None (default) keeps
    # stop_loss_pct fixed; a number makes the actual per-trade stop
    # atr_multiplier times that symbol's own recent ATR, clamped to
    # [min_atr_stop_pct, max_atr_stop_pct] - position size (computed FROM
    # stop distance) becomes volatility-aware as a direct consequence.
    atr_multiplier: Optional[float] = 2.0
    min_atr_stop_pct: float = 0.005
    max_atr_stop_pct: float = 0.05
    # Confidence-based sizing via the Kelly criterion: once at least this
    # many real trades exist, position size is capped at what this
    # system's own actual win rate/payoff ratio statistically justifies -
    # a CAP only, never a reason to size up beyond risk-based sizing.
    # None disables it entirely (opt-in).
    kelly_min_trades: Optional[int] = 15
    kelly_lookback_trades: int = 30
    # Crisis correlation override (trading_assistant.risk.crisis): rolling
    # Pearson correlation (correlation_threshold/correlation_lookback_bars
    # above) is estimated from calm-period data and understates how
    # correlated everything becomes in a genuine market-wide panic. None
    # disables this entirely (opt-in, backward compatible). A number (e.g.
    # 0.15) means: if crisis_symbol drops that much within
    # crisis_window_hours, treat EVERY held position as perfectly
    # correlated with any new candidate for crisis_duration_hours (or
    # until crisis_symbol recovers, whichever comes first) - a blunt
    # safety overlay, not a replacement for the correlation model above.
    crisis_drop_threshold: Optional[float] = None
    crisis_symbol: str = "BTC/USDT"
    crisis_window_hours: float = 1.0
    crisis_duration_hours: float = 24.0


@dataclass
class MarketDataConfig:
    """Market-data integrity guard (trading_assistant.market_data.integrity):
    a fetch can nominally "succeed" (no exception, non-empty frame) while
    still returning garbage - corrupted OHLC, out-of-order/duplicate
    timestamps, an implausible single-bar move, or a feed that's quietly
    stopped producing new bars. Disabled by default (opt-in, same
    convention as every other new risk gate in this system)."""

    enable_integrity_checks: bool = False
    # A >50% single-bar move on a symbol liquid enough to be tracked at all
    # is far more consistent with a data glitch than genuine price action.
    max_single_bar_move_pct: Optional[float] = 0.5
    # Multiple of the timeframe's own bar length allowed before the latest
    # bar counts as stale (accounts for bars closing a little after their
    # nominal boundary, plus normal poll timing jitter).
    max_staleness_multiple: Optional[float] = 3.0
    # Panic/illiquid market-condition overlay (classify_market_condition) -
    # not gated by enable_integrity_checks above: that flag is about
    # whether DATA can be trusted at all, this is about whether current
    # CONDITIONS are safe to trade regardless of data quality. Harmless
    # under normal conditions by construction, so always active; these
    # just tune where the line is.
    panic_atr_threshold: float = 0.08
    illiquid_volume_threshold: float = 0.3
    # Regime misclassification monitoring: alerts when the live regime
    # label flip-flops too often across the trailing regime_history_window
    # polls to be a dependable characterization (as opposed to panic/
    # illiquid above, which are about volatility/volume, not the LABEL
    # itself being unstable). None disables alerting (opt-in); the rolling
    # history/regime_churn_rate() query always works regardless.
    regime_history_window: int = 20
    regime_churn_alert_threshold: Optional[float] = 0.5


@dataclass
class PortfolioConfig:
    initial_capital: float = 10000.0
    # Real trading fees/slippage - defaults match Binance's standard retail
    # spot fee (0.1%/side without a BNB discount) and the same slippage
    # assumption BacktestEngineV2 already uses, so the real portfolio's own
    # equity curve (and therefore its Sharpe/drawdown in the readiness
    # scorecard) isn't silently overstating performance by ignoring costs
    # the exchange actually charges.
    commission_rate: float = 0.001
    slippage_rate: float = 0.0005
    # LIVE MODE ONLY (see Orchestrator.is_live/sync_capital_with_exchange):
    # below this real, synced account balance, the main trading decision
    # paper-trades against a dedicated fake-money portfolio instead of
    # attempting a real order that would just be rejected for insufficient
    # balance - "gather real evidence using real market data" while waiting
    # for the account to actually be funded, then switch to real execution
    # automatically once synced capital clears this. $5 is a practical
    # floor, not a magic number - Binance enforces its own per-symbol
    # minimum notional (varies, but is at least a few dollars for every
    # pair this system trades), so anything materially below that could
    # never place a real order anyway even with risk approval.
    min_live_capital_usd: float = 5.0


@dataclass
class PredictionConfig:
    # The probability-of-up threshold a live prediction must clear to
    # become a BUY/SELL signal at all (HOLD otherwise) - this was never
    # actually configurable before (PredictionAgent() was constructed bare
    # in run_system.py, silently always using its 0.6/0.4 class defaults).
    # That combination produced exactly ONE real BUY signal across 1,163
    # live decisions over multiple days: measured live, this system's
    # promoted models' probability_up output ranged only ~0.40-0.59 (mean
    # 0.45, stdev ~0.06) - essentially never reaching 0.6, since a model
    # walk-forward-validated on testnet's currently-limited history (see
    # LearningConfig.min_folds_required's docstring) has real but modest
    # edge, not the strong class separation 0.6/0.4 implicitly assumes.
    # 0.54/0.46 is calibrated to that ACTUAL observed distribution rather
    # than an arbitrary round number: ~5% of real predictions cleared 0.54
    # (still a real, above-median conviction filter - not "trade on any
    # wobble"), enough to generate genuine trading activity and a real
    # track record instead of a system that mathematically could never
    # trade. Revisit once more promoted models accumulate more validation
    # history and their probability spread widens.
    buy_threshold: float = 0.54
    sell_threshold: float = 0.46


@dataclass
class LearningConfig:
    retrain_interval_hours: float = 24.0
    min_sharpe_to_promote: float = 0.5
    max_drawdown_to_promote: float = 0.25
    # Walk-forward validation: a fixed-size training window rolled forward
    # across history, training + backtesting fresh on each non-overlapping
    # slice, so promotion reflects consistency across many market regimes
    # rather than one lucky train/test split. See LearningAgent's docstring.
    train_window_bars: int = 500
    test_window_bars: int = 100
    # How many folds to fetch enough history for (drives total_bars in
    # retrain_from_live). More folds = more statistical confidence but
    # proportionally more retrain wall-clock time (each fold trains a fresh
    # model) - tune deliberately, don't just raise this blindly.
    target_folds: int = 8
    # Lowered from 5: Binance testnet's actual available OHLCV history for
    # most tracked symbols (~880 hourly bars, capped at a fixed start date -
    # confirmed by direct query, not assumed) only supports 3 folds given
    # train_window_bars/test_window_bars above, so 5 was an unreachable bar
    # that blocked every promotion regardless of how good a candidate
    # looked (0 promotions across 93 real retrain attempts). 3 matches what
    # the tournament's own min_folds_required already uses (see `tournament`
    # section below) and is the accepted minimum for basic walk-forward
    # cross-validation - clears_threshold_significantly already has a
    # documented small-sample fallback (plain median comparison) below
    # MIN_FOLDS_FOR_TEST=5, so this doesn't skip validation, just uses a
    # less statistically powerful check until testnet's history naturally
    # grows past ~1000 bars and unlocks a 5th fold on its own.
    min_folds_required: int = 3
    min_pct_folds_profitable: float = 0.5
    # A fold with 0-2 trades in its 100-bar test window produces a
    # Sharpe/return number that's mostly noise (a coin-flip's worth of
    # observations), but previously counted toward min_folds_required and
    # the median-Sharpe/pct-profitable aggregates exactly the same as a
    # fold with 20+ trades - a real audit finding: some symbols had as few
    # as 9-10 TOTAL trades across all 8 folds combined, yet were weighed
    # identically to symbols with robust per-fold activity. Folds below
    # this trade count are dropped before any aggregate is computed, so
    # min_folds_required now means "this many folds that actually
    # observed enough trades to say something," not just "this many folds
    # ran." 0 disables the filter (every fold counts, prior behavior).
    min_trades_per_fold: int = 3
    # Live-performance-triggered rollback: once at least this many real
    # trades have closed for a symbol since its model was promoted,
    # compare their actual win rate against what walk-forward validated -
    # if it's decayed to less than decay_win_rate_fraction of that AND
    # the recent window is net-losing, automatically roll back to the
    # previously-promoted model version. None disables it entirely.
    min_live_trades_for_decay_check: Optional[int] = 15
    decay_win_rate_fraction: float = 0.5
    decay_lookback_trades: int = 30


@dataclass
class LoggingConfig:
    level: str = "INFO"
    file: str = "logs/trading_system.log"


@dataclass
class SymbolDiscoveryConfig:
    """Dynamic watchlist selection: instead of only ever trading a hand-picked
    static symbol list, periodically discover Binance's full market list and
    select the most liquid subset. `Config.symbols` still applies as the
    initial seed (used immediately at startup before the first discovery
    completes, and as the fallback if discovery is disabled)."""

    enabled: bool = False
    quote_currency: str = "USDT"
    min_24h_volume_usd: float = 5_000_000.0
    max_symbols: int = 20
    refresh_interval_hours: float = 24.0


@dataclass
class TournamentConfig:
    """Regime-aware multi-strategy tournament (StrategyManager). By default
    (`enabled: true`, `drive_live_trading: false`) it runs OBSERVATION-ONLY
    on its own cadence, building a stability track record (does the same
    candidate keep winning a given regime across repeated re-runs?) without
    ever influencing a live trade decision.

    `drive_live_trading: true` is a SEPARATE, deliberate escalation: the
    tournament's regime winner (the "champion") then drives the real
    decision through the existing risk/execution/portfolio pipeline -
    still testnet by default via `mode`, exactly like every other live
    path in this system - while every other candidate (the "challengers")
    paper-trades the same live data in parallel via ChampionChallengerAgent,
    never touching real execution. Per this system's validation history:
    do not flip this on until winner_history/stability_score across
    multiple symbols and regimes, over a meaningful stretch of time, shows
    the tournament isn't just flip-flopping."""

    # True to match this class's own docstring above ("By default...it
    # runs OBSERVATION-ONLY") - was False until this was caught by an
    # audit: config.yaml's own explicit `enabled: true` masked the drift
    # in practice, but load_config falling back to bare dataclass defaults
    # (e.g. a caller-supplied --config path that doesn't exist) would have
    # silently disabled the tournament/champion-challenger pipeline while
    # every comment and the docstring claimed it was on.
    enabled: bool = True
    drive_live_trading: bool = False
    train_window_bars: int = 500
    test_window_bars: int = 100
    target_folds: int = 10
    min_folds_required: int = 3
    min_sharpe_to_promote: float = 0.5
    min_pct_folds_profitable: float = 0.5
    allow_short: bool = False
    refresh_interval_hours: float = 24.0
    paper_capital: float = 10000.0
    min_trades_to_compare: int = 5
    # Same real-world assumptions as PortfolioConfig's commission_rate/
    # slippage_rate (and BacktestEngineV2's defaults) - a challenger must
    # never look better than the champion just because its paper trades
    # skip the transaction costs the champion actually pays.
    paper_commission_rate: float = 0.001
    paper_slippage_rate: float = 0.0005
    # Champion-switching hysteresis: without this, a symbol/regime's
    # champion is re-selected from scratch every tournament run with no
    # memory of who held it before - two candidates a hair's-breadth apart
    # in median Sharpe (noise) could trade places run after run, each
    # switch carrying a real cost (a live position opened under one
    # strategy's logic, closed under a completely different one's). An
    # incumbent that still clears the promotion bar is only replaced once
    # it has held the position for at least champion_switch_min_runs runs
    # AND the challenger beats it by at least champion_switch_margin.
    champion_switch_min_runs: int = 3
    champion_switch_margin: float = 0.1


@dataclass
class ReadinessConfig:
    """Thresholds for the go-live readiness scorecard - turns "are we ready
    yet" from a recurring judgment call into a computed, auditable answer.
    Every default here is deliberately justified, not arbitrary:

    - min_tournament_runs: at the hourly cadence tournament.refresh_interval_hours
      is set to, 20 runs is under a day - a low bar deliberately, so the
      scorecard starts reporting real (if early) numbers quickly rather than
      showing nothing for a week; raise this once you want a stricter gate.
    - min_stability_score: the winner must stay the same at least 80% of the
      time between consecutive re-runs - a tournament that flip-flops half
      the time has no real signal to hand to a live decision.
    - min_real_trades: enough completed real round-trips to say anything
      statistically, not just "it traded once and didn't lose money."
    - min_real_sharpe: reuses the exact bar (0.5) already used everywhere
      else in this system to promote a model or a tournament candidate -
      consistent standard, not a new one invented just for this.
    - min_elapsed_days: matches this system's own stated philosophy -
      consistency over real elapsed time, not a few hours of quiet.
    - min_records_for_correlation: how many (symbol, candidate) pairs with
      enough live paper trades are needed before the backtest-vs-live
      correlation check means anything - a correlation coefficient over a
      handful of points is noise, not signal.
    - min_live_trades_for_correlation: matches
      ChampionChallengerAgent's own min_trades_to_compare default (5) - a
      candidate's live paper Sharpe isn't trustworthy with fewer trades
      than that either.
    """

    min_tournament_runs: int = 20
    min_stability_score: float = 0.8
    min_real_trades: int = 10
    min_real_sharpe: float = 0.5
    min_elapsed_days: float = 7.0
    min_records_for_correlation: int = 5
    min_live_trades_for_correlation: int = 5


@dataclass
class RetentionConfig:
    """Time-based pruning for high-frequency/operational tables, run on its
    own background loop (Orchestrator._retention_loop) so the database
    doesn't grow forever across weeks/months of unattended 24/7 operation.
    Audit-critical tables (trades, trade_audit, alerts, retrain_events,
    circuit_breaker_events) are never touched, regardless of this config -
    see Journal._PRUNABLE_TABLES for the exact split and why."""

    enabled: bool = True
    retention_days: int = 180
    run_interval_hours: float = 24.0


@dataclass
class Config:
    mode: str = "testnet"
    exchange: ExchangeConfig = field(default_factory=ExchangeConfig)
    symbols: List[str] = field(default_factory=lambda: ["BTC/USDT"])
    # 1h/300s match config.yaml's validated defaults (see its comments) -
    # re-validate with a fresh retrain before changing.
    timeframe: str = "1h"
    poll_interval_seconds: float = 300.0
    # The FAST position-monitor loop's cadence (see Orchestrator.
    # _position_monitor_loop) - independent of, and much faster than,
    # poll_interval_seconds above. Only touches symbols with an open
    # position (trailing stop / partial profit / trend exit), never
    # generates a new signal or retrains anything. None disables it
    # entirely (those checks then only run once per poll_interval_seconds,
    # inside the main cycle, same as before this existed).
    position_monitor_interval_seconds: Optional[float] = 20.0
    risk: RiskConfig = field(default_factory=RiskConfig)
    market_data: MarketDataConfig = field(default_factory=MarketDataConfig)
    portfolio: PortfolioConfig = field(default_factory=PortfolioConfig)
    prediction: PredictionConfig = field(default_factory=PredictionConfig)
    learning: LearningConfig = field(default_factory=LearningConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    symbol_discovery: SymbolDiscoveryConfig = field(default_factory=SymbolDiscoveryConfig)
    tournament: TournamentConfig = field(default_factory=TournamentConfig)
    readiness: ReadinessConfig = field(default_factory=ReadinessConfig)
    retention: RetentionConfig = field(default_factory=RetentionConfig)

    @property
    def is_live(self) -> bool:
        return self.mode == "live"

    def get_api_credentials(self) -> Tuple[Optional[str], Optional[str]]:
        """Read the exchange API key/secret from the env vars named in config."""
        api_key = os.environ.get(self.exchange.api_key_env)
        api_secret = os.environ.get(self.exchange.api_secret_env)
        return api_key, api_secret


def load_config(path: Optional[os.PathLike] = None, *, env_file: Optional[os.PathLike] = None) -> Config:
    """Load system config from YAML plus secrets from a .env file.

    Missing files fall back to defaults so the system is runnable in
    testnet/paper mode with zero setup.
    """
    load_dotenv(dotenv_path=env_file)  # no-op if the file doesn't exist

    config_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    data = {}
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    elif path is not None:
        # A caller-supplied path (e.g. --config) that doesn't exist is very
        # likely a typo, not a deliberate "run with defaults" choice - the
        # no-argument case (DEFAULT_CONFIG_PATH missing) stays silent by
        # design (documented above: zero-setup testnet/paper mode), but an
        # explicit path silently vanishing into full defaults would drop
        # most of this system's safety net (integrity checks, tournament,
        # symbol discovery, trailing stop, the hard drawdown circuit
        # breaker) with no indication anything went wrong.
        logger.warning(
            "config path %s does not exist - falling back to built-in defaults for everything it would "
            "have set (integrity checks, tournament, symbol discovery, trailing stop/partial-profit, and "
            "the hard drawdown circuit breaker are all OFF by default)",
            config_path,
        )

    mode = data.get("mode", "testnet")
    if mode not in ("testnet", "live"):
        raise ValueError(f"config 'mode' must be 'testnet' or 'live', got {mode!r}")

    defaults = Config()
    return Config(
        mode=mode,
        exchange=ExchangeConfig(**data.get("exchange", {})),
        symbols=data.get("symbols") or defaults.symbols,
        timeframe=data.get("timeframe", defaults.timeframe),
        poll_interval_seconds=data.get("poll_interval_seconds", defaults.poll_interval_seconds),
        position_monitor_interval_seconds=data.get(
            "position_monitor_interval_seconds", defaults.position_monitor_interval_seconds
        ),
        risk=RiskConfig(**data.get("risk", {})),
        market_data=MarketDataConfig(**data.get("market_data", {})),
        portfolio=PortfolioConfig(**data.get("portfolio", {})),
        prediction=PredictionConfig(**data.get("prediction", {})),
        learning=LearningConfig(**data.get("learning", {})),
        logging=LoggingConfig(**data.get("logging", {})),
        symbol_discovery=SymbolDiscoveryConfig(**data.get("symbol_discovery", {})),
        tournament=TournamentConfig(**data.get("tournament", {})),
        readiness=ReadinessConfig(**data.get("readiness", {})),
        retention=RetentionConfig(**data.get("retention", {})),
    )
