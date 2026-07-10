"""MarketMonitorAgent: multi-symbol OHLCV polling and regime detection.

Regime detection is derived from the SAME build_features() output used for
live prediction and training. Previously this agent computed its own
independent SMA/RSI with independently-configurable periods that had no
effect on what the model actually saw - tuning them looked like it should
matter but silently didn't, and it duplicated indicator computation that
build_features already does.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import ccxt
import pandas as pd

from trading_assistant.agents.alerting import AlertManager
from trading_assistant.agents.features import build_features, classify_market_condition, classify_regime
from trading_assistant.market_data.feed import LiveFeed
from trading_assistant.market_data.integrity import detect_anomalies

logger = logging.getLogger(__name__)


@dataclass
class MarketSnapshot:
    symbol: str
    frame: pd.DataFrame
    trend_strength: float  # sma_fast_vs_slow ratio from build_features; >0 up, <0 down
    rsi: float  # 0-100
    regime: str  # "trending_up" | "trending_down" | "ranging"
    # An OVERLAY on top of regime (see classify_market_condition's
    # docstring) - "panic" | "illiquid" | None. Orchestrator sits out
    # entirely on either, regardless of what regime/signal would
    # otherwise apply.
    market_condition: Optional[str] = None


class MarketMonitorAgent:
    """Polls live OHLCV for each configured symbol and derives a regime snapshot.

    `feeds` lets tests inject fake feed objects (anything with a
    `fetch_ohlcv(limit=...)` method) instead of hitting Binance.
    """

    def __init__(
        self,
        symbols: List[str],
        timeframe: str = "1m",
        history_size: int = 200,
        regime_window_bars: int = 100,
        feeds: Optional[Dict[str, Any]] = None,
        exchange: Optional[Any] = None,
        # Market-data integrity guard (trading_assistant.market_data.integrity):
        # opt-in as a whole (disabled by default - existing tests/callers
        # unaffected), same convention as every other new risk gate in this
        # system. Once enabled, structural OHLC/timestamp checks always run;
        # max_single_bar_move_pct/max_staleness_multiple further gate the
        # two judgment-based checks within that (None disables just that one).
        enable_integrity_checks: bool = False,
        max_single_bar_move_pct: Optional[float] = None,
        max_staleness_multiple: Optional[float] = None,
        alert_manager: Optional[AlertManager] = None,
        # Panic/illiquid market-condition overlay thresholds - see
        # classify_market_condition's docstring. Harmless under normal
        # conditions by construction (returns None unless genuinely
        # extreme), so unlike enable_integrity_checks this has no on/off
        # switch; these two just let an operator tune where the line is.
        panic_atr_threshold: float = 0.08,
        illiquid_volume_threshold: float = 0.3,
        # Regime misclassification monitoring: tracks each symbol's live
        # regime label poll-to-poll and alerts when it's flip-flopping too
        # often to be a dependable characterization of the market (as
        # opposed to panic/illiquid above, which are about volatility/volume,
        # not about the trend/range LABEL itself being unstable). None
        # disables alerting entirely (opt-in, same convention as every
        # other new risk/monitoring gate in this system) - the rolling
        # history and regime_churn_rate() query still work either way.
        regime_history_window: int = 20,
        regime_churn_alert_threshold: Optional[float] = None,
    ):
        self.symbols = symbols
        self.timeframe = timeframe
        self.history_size = history_size
        self.enable_integrity_checks = enable_integrity_checks
        self.max_single_bar_move_pct = max_single_bar_move_pct
        self.max_staleness_multiple = max_staleness_multiple
        self.alert_manager = alert_manager
        self.panic_atr_threshold = panic_atr_threshold
        self.illiquid_volume_threshold = illiquid_volume_threshold
        self.regime_history_window = regime_history_window
        self.regime_churn_alert_threshold = regime_churn_alert_threshold
        self._regime_history: Dict[str, List[str]] = {}
        # Unconditional (not gated behind enable_integrity_checks): used
        # both for staleness detection (opt-in, needs max_staleness_multiple
        # too) AND for detecting a still-forming last bar in
        # _build_snapshot's market_condition check, which - unlike
        # enable_integrity_checks - is "always active" by this class's own
        # design (see illiquid_volume_threshold's docstring above).
        self._timeframe_seconds: Optional[float] = None
        try:
            self._timeframe_seconds = ccxt.Exchange.parse_timeframe(timeframe)
        except Exception:  # noqa: BLE001 - an unparseable timeframe just disables both checks
            logger.warning("could not parse timeframe", extra={"context": {"timeframe": timeframe}})
        # Passed to any LiveFeed constructed on demand (both here and in
        # update_symbols) so live regime monitoring reads from the SAME
        # exchange (and testnet/mainnet mode) real execution uses - a bare
        # LiveFeed() otherwise defaults to an unsandboxed, always-mainnet
        # ccxt instance regardless of config.mode.
        self._exchange = exchange
        # Should match StrategyManager.test_window_bars when used alongside
        # the strategy tournament: the tournament classifies a fold's regime
        # from a WINDOWED average over its test period, not one bar. Using a
        # single live bar here would flip the live regime on ordinary
        # bar-to-bar noise (e.g. one pullback candle inside an otherwise
        # clean uptrend), making a validated champion sit out far more than
        # its real edge warrants purely from a definitional mismatch between
        # how "regime" was validated and how it's checked live.
        self.regime_window_bars = regime_window_bars
        self._feeds: Dict[str, Any] = feeds or {
            symbol: LiveFeed(symbol=symbol, timeframe=timeframe, exchange=exchange) for symbol in symbols
        }
        self._history: Dict[str, pd.DataFrame] = {}
        self.failed_symbols: List[str] = []

    def update_symbols(self, symbols: List[str], feeds: Optional[Dict[str, Any]] = None) -> None:
        """Replace the tracked symbol list (e.g. from SymbolDiscoveryAgent).

        Feeds for newly added symbols come from `feeds` if provided (for
        tests), otherwise a real LiveFeed is constructed. Feeds/history for
        symbols no longer tracked are dropped.
        """
        new_symbols = list(dict.fromkeys(symbols))  # dedupe, preserve order
        feeds = feeds or {}
        for symbol in new_symbols:
            if symbol not in self._feeds:
                self._feeds[symbol] = feeds.get(symbol) or LiveFeed(
                    symbol=symbol, timeframe=self.timeframe, exchange=self._exchange
                )
        for symbol in list(self._feeds.keys()):
            if symbol not in new_symbols:
                self._feeds.pop(symbol, None)
                self._history.pop(symbol, None)
        self.symbols = new_symbols

    def poll(self) -> Dict[str, MarketSnapshot]:
        """Fetch the latest bars for every symbol and return a snapshot per symbol.

        One symbol's fetch failure does not prevent polling the others -
        each symbol is independently try/excepted and recorded in
        `failed_symbols` so callers (the orchestrator) can feed persistent
        market-data failures into the circuit breaker, same as execution
        failures.
        """
        snapshots: Dict[str, MarketSnapshot] = {}
        self.failed_symbols = []
        for symbol in self.symbols:
            try:
                frame = self._feeds[symbol].fetch_ohlcv(limit=self.history_size)
            except Exception as exc:  # noqa: BLE001 - one symbol's feed error must not skip the rest
                logger.error(
                    "failed to fetch market data", extra={"context": {"symbol": symbol, "error": str(exc)}}
                )
                self.failed_symbols.append(symbol)
                continue

            if frame is None or frame.empty:
                logger.warning("empty OHLCV frame", extra={"context": {"symbol": symbol}})
                self.failed_symbols.append(symbol)
                continue

            anomalies = (
                detect_anomalies(
                    frame,
                    # Staleness checking stays opt-in via max_staleness_multiple
                    # specifically (unchanged gating) - _timeframe_seconds
                    # itself is now computed unconditionally (see __init__)
                    # because _build_snapshot's market_condition check also
                    # needs it, but that must not silently turn ON staleness
                    # checking for a caller that left max_staleness_multiple
                    # at its default (off).
                    timeframe_seconds=self._timeframe_seconds if self.max_staleness_multiple is not None else None,
                    max_single_bar_move_pct=self.max_single_bar_move_pct,
                )
                if self.enable_integrity_checks
                else []
            )
            if anomalies:
                # Untrustworthy data is its own category (see integrity.py's
                # docstring) - treated the same as a fetch failure: skipped
                # this cycle and counted toward the circuit breaker, rather
                # than silently feeding a corrupted frame into regime
                # classification and a live trading decision.
                logger.warning(
                    "market data failed integrity checks - skipping this symbol this cycle",
                    extra={"context": {"symbol": symbol, "issues": anomalies}},
                )
                if self.alert_manager is not None:
                    self.alert_manager.raise_alert(
                        "market_data_integrity",
                        "warning",
                        f"{symbol}: market data failed integrity checks: {'; '.join(anomalies)}",
                        context={"symbol": symbol, "issues": anomalies},
                        dedupe_key=f"market_data_integrity:{symbol}",
                    )
                self.failed_symbols.append(symbol)
                continue

            self._history[symbol] = frame
            snapshot = self._build_snapshot(symbol, frame)
            snapshots[symbol] = snapshot
            self._track_regime(symbol, snapshot.regime)
        return snapshots

    def _track_regime(self, symbol: str, regime: str) -> None:
        history = self._regime_history.setdefault(symbol, [])
        history.append(regime)
        excess = len(history) - self.regime_history_window
        if excess > 0:
            del history[:excess]

        if self.regime_churn_alert_threshold is None or len(history) < self.regime_history_window:
            return
        churn_rate = self.compute_regime_churn_rate(history)
        if churn_rate is not None and churn_rate >= self.regime_churn_alert_threshold:
            logger.warning(
                "regime classification is flip-flopping - live label may be unreliable",
                extra={"context": {"symbol": symbol, "churn_rate": churn_rate, "history": history}},
            )
            if self.alert_manager is not None:
                self.alert_manager.raise_alert(
                    "regime_churn",
                    "warning",
                    f"{symbol}: regime classification changed {churn_rate:.0%} of the last "
                    f"{len(history) - 1} polls - the live trend/range label may not be dependable right now.",
                    context={"symbol": symbol, "churn_rate": churn_rate, "history": history},
                    dedupe_key=f"regime_churn:{symbol}",
                )

    def regime_churn_rate(self, symbol: str) -> Optional[float]:
        """Fraction of consecutive live polls (within regime_history_window)
        where the regime classification changed from the previous poll -
        see compute_regime_churn_rate. Public so the dashboard/tests can
        query current churn without triggering the alert side effect."""
        return self.compute_regime_churn_rate(self._regime_history.get(symbol, []))

    @staticmethod
    def compute_regime_churn_rate(history: List[str]) -> Optional[float]:
        """Pure function: fraction of consecutive entries in `history` that
        differ from the entry before them. High churn on an actively-traded
        symbol signals the market is genuinely borderline right now
        (trend_strength/adx hovering near classify_regime's threshold),
        making the regime LABEL itself unreliable - distinct from
        panic/illiquid, which flag untrustworthy volatility/volume rather
        than an unstable classification. Returns None if fewer than 2
        entries are recorded yet, mirroring StrategyManager.compute_stability_score's
        same guard for the same reason: this is a claim about repeated
        behavior over time, not something one poll can establish."""
        if len(history) < 2:
            return None
        changes = sum(1 for i in range(1, len(history)) if history[i] != history[i - 1])
        return changes / (len(history) - 1)

    def _build_snapshot(self, symbol: str, frame: pd.DataFrame) -> MarketSnapshot:
        featured = build_features(frame)
        last = featured.iloc[-1]

        trend_strength = float(last["sma_fast_vs_slow"]) if pd.notna(last["sma_fast_vs_slow"]) else float("nan")
        rsi = float(last["rsi_normalized"] * 100.0) if pd.notna(last["rsi_normalized"]) else float("nan")

        # Regime classification uses a WINDOWED average, not the single most
        # recent bar - see regime_window_bars' docstring above for why.
        window = featured.tail(self.regime_window_bars)
        window_trend_strength = float(window["sma_fast_vs_slow"].mean()) if not window.empty else float("nan")
        window_adx_strength = float(window["adx_strength"].mean()) if not window.empty else float("nan")
        regime = classify_regime(window_trend_strength, window_adx_strength)

        # Deliberately the LATEST bar, not a windowed average like regime
        # above - "panic right now" is an acute condition; smoothing it
        # over regime_window_bars would defeat the point of catching it.
        # But "latest bar" here must mean the latest CLOSED bar: ccxt's
        # fetch_ohlcv always includes the currently-forming candle as the
        # last row (confirmed - nothing anywhere trims it), so polling
        # mid-interval (poll_interval_seconds=300 against timeframe="1h"
        # by default - this fires on the vast majority of polls) makes
        # relative_volume trivially small purely because little wall-clock
        # time has elapsed in that bar, not because the market is actually
        # thin - falsely classifying a perfectly liquid symbol as
        # "illiquid" and sitting out on it for no real reason. Regime
        # above is safe as-is (a windowed average dilutes one partial
        # bar's effect); this acute, single-bar check is not.
        condition_row = last
        if self._timeframe_seconds and len(featured) >= 2:
            last_bar_timestamp = pd.Timestamp(last["timestamp"])
            if last_bar_timestamp.tzinfo is None:
                last_bar_timestamp = last_bar_timestamp.tz_localize("UTC")
            last_bar_close_time = last_bar_timestamp + pd.Timedelta(seconds=self._timeframe_seconds)
            if pd.Timestamp.now(tz="UTC") < last_bar_close_time:
                condition_row = featured.iloc[-2]
        market_condition = classify_market_condition(
            float(condition_row["atr_pct"]) if pd.notna(condition_row["atr_pct"]) else float("nan"),
            float(condition_row["relative_volume"]) if pd.notna(condition_row["relative_volume"]) else float("nan"),
            panic_atr_threshold=self.panic_atr_threshold,
            illiquid_volume_threshold=self.illiquid_volume_threshold,
        )

        return MarketSnapshot(
            symbol=symbol,
            frame=frame,
            trend_strength=trend_strength,
            rsi=rsi,
            regime=regime,
            market_condition=market_condition,
        )

    def get_returns_frame(self) -> pd.DataFrame:
        """Aligned close-price pct-change series per tracked symbol, for portfolio-level risk checks."""
        series = {}
        for symbol, frame in self._history.items():
            if frame.empty:
                continue
            closes = frame.set_index("timestamp")["close"]
            series[symbol] = closes.pct_change().dropna()
        if not series:
            return pd.DataFrame()
        return pd.DataFrame(series).dropna(how="all")
