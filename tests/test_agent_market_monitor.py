import itertools

import pandas as pd
import pytest

from trading_assistant.agents.market_monitor import MarketMonitorAgent


class FakeFeed:
    def __init__(self, closes):
        self.closes = closes

    def fetch_ohlcv(self, limit=100):
        n = len(self.closes)
        return pd.DataFrame(
            {
                "timestamp": pd.date_range("2026-01-01", periods=n, freq="min", tz="UTC"),
                "open": self.closes,
                "high": [c + 1 for c in self.closes],
                "low": [c - 1 for c in self.closes],
                "close": self.closes,
                "volume": [100] * n,
            }
        )


class EmptyFeed:
    def fetch_ohlcv(self, limit=100):
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])


class FailingFeed:
    def fetch_ohlcv(self, limit=100):
        raise ConnectionError("simulated network error")


def test_poll_returns_snapshot_with_uptrend_regime():
    closes = [float(i) for i in range(1, 41)]  # steadily increasing, enough bars for build_features' warmup
    agent = MarketMonitorAgent(symbols=["BTC/USDT"], feeds={"BTC/USDT": FakeFeed(closes)})

    snapshots = agent.poll()

    assert "BTC/USDT" in snapshots
    snapshot = snapshots["BTC/USDT"]
    assert snapshot.regime == "trending_up"
    assert snapshot.trend_strength > 0


def test_poll_returns_downtrend_regime():
    closes = [float(i) for i in range(40, 0, -1)]  # steadily decreasing
    agent = MarketMonitorAgent(symbols=["ETH/USDT"], feeds={"ETH/USDT": FakeFeed(closes)})

    snapshots = agent.poll()

    snapshot = snapshots["ETH/USDT"]
    assert snapshot.regime == "trending_down"
    assert snapshot.trend_strength < 0


def test_poll_skips_symbols_with_empty_feed():
    agent = MarketMonitorAgent(symbols=["BTC/USDT"], feeds={"BTC/USDT": EmptyFeed()})

    snapshots = agent.poll()

    assert snapshots == {}
    assert agent.failed_symbols == ["BTC/USDT"]


def test_poll_records_failed_symbols_when_fetch_raises():
    agent = MarketMonitorAgent(symbols=["BTC/USDT"], feeds={"BTC/USDT": FailingFeed()})

    snapshots = agent.poll()

    assert snapshots == {}
    assert agent.failed_symbols == ["BTC/USDT"]


def test_poll_continues_past_a_failing_symbol_to_poll_others():
    """Regression test: one symbol's fetch exception used to propagate
    uncaught out of poll(), aborting the loop before any later symbols
    were ever polled that cycle."""
    closes = [float(i) for i in range(1, 41)]
    agent = MarketMonitorAgent(
        symbols=["BTC/USDT", "ETH/USDT"],
        feeds={"BTC/USDT": FailingFeed(), "ETH/USDT": FakeFeed(closes)},
    )

    snapshots = agent.poll()

    assert "BTC/USDT" not in snapshots
    assert "ETH/USDT" in snapshots
    assert agent.failed_symbols == ["BTC/USDT"]


def test_get_returns_frame_aligned_across_symbols():
    closes = [float(i) for i in range(1, 11)]
    agent = MarketMonitorAgent(
        symbols=["BTC/USDT", "ETH/USDT"],
        feeds={"BTC/USDT": FakeFeed(closes), "ETH/USDT": FakeFeed(closes)},
    )
    agent.poll()

    returns = agent.get_returns_frame()

    assert not returns.empty
    assert set(returns.columns) == {"BTC/USDT", "ETH/USDT"}


def test_update_symbols_adds_new_symbol_using_injected_feed():
    closes = [float(i) for i in range(1, 21)]
    agent = MarketMonitorAgent(symbols=["BTC/USDT"], feeds={"BTC/USDT": FakeFeed(closes)})

    agent.update_symbols(["BTC/USDT", "ETH/USDT"], feeds={"ETH/USDT": FakeFeed(closes)})

    assert agent.symbols == ["BTC/USDT", "ETH/USDT"]
    snapshots = agent.poll()
    assert "ETH/USDT" in snapshots


def test_update_symbols_drops_feed_and_history_for_removed_symbols():
    closes = [float(i) for i in range(1, 21)]
    agent = MarketMonitorAgent(
        symbols=["BTC/USDT", "ETH/USDT"],
        feeds={"BTC/USDT": FakeFeed(closes), "ETH/USDT": FakeFeed(closes)},
    )
    agent.poll()
    assert "ETH/USDT" in agent._history

    agent.update_symbols(["BTC/USDT"])

    assert agent.symbols == ["BTC/USDT"]
    assert "ETH/USDT" not in agent._feeds
    assert "ETH/USDT" not in agent._history


def test_update_symbols_keeps_existing_feed_for_symbols_still_tracked():
    """A symbol present both before and after an update must keep its
    existing feed/history rather than being reconstructed from scratch."""
    closes = [float(i) for i in range(1, 21)]
    original_feed = FakeFeed(closes)
    agent = MarketMonitorAgent(symbols=["BTC/USDT"], feeds={"BTC/USDT": original_feed})

    agent.update_symbols(["BTC/USDT", "ETH/USDT"], feeds={"ETH/USDT": FakeFeed(closes)})

    assert agent._feeds["BTC/USDT"] is original_feed


def test_regime_uses_windowed_average_not_just_the_last_bar(monkeypatch):
    """Regression test found via a real Binance testnet run: classifying
    regime from only the single most recent bar makes it flip on ordinary
    noise (one pullback bar inside an otherwise clean uptrend), which would
    make a StrategyManager-validated champion sit out far more than its
    real edge warrants - the tournament classifies a fold's regime from a
    WINDOWED average, so live classification must match that definition."""
    closes = [100.0 + i for i in range(30)]

    def fake_build_features(frame, *args, **kwargs):
        result = frame.copy()
        # 29 bars strongly trending up, the single LAST bar flips negative -
        # simulates one noisy pullback candle inside an otherwise clean uptrend.
        result["sma_fast_vs_slow"] = [0.05] * 29 + [-0.05]
        result["adx_strength"] = [0.60] * 30
        result["rsi_normalized"] = [0.5] * 30
        result["atr_pct"] = [0.01] * 30
        result["relative_volume"] = [1.0] * 30
        return result

    monkeypatch.setattr("trading_assistant.agents.market_monitor.build_features", fake_build_features)

    agent = MarketMonitorAgent(symbols=["BTC/USDT"], regime_window_bars=20, feeds={"BTC/USDT": FakeFeed(closes)})

    snapshots = agent.poll()

    # the single last bar alone would classify as trending_down, but the
    # 20-bar windowed average (dominated by 19 strongly positive bars) must
    # still classify as trending_up.
    assert snapshots["BTC/USDT"].regime == "trending_up"
    # trend_strength itself still reports the raw last-bar value (for
    # display/logging) - only regime classification is windowed.
    assert snapshots["BTC/USDT"].trend_strength == -0.05


def test_default_feed_construction_passes_through_the_injected_exchange():
    """Regression: MarketMonitorAgent defaulted every symbol's LiveFeed to a
    bare, always-mainnet ccxt instance since no exchange was ever threaded
    through - found via an audit while execution correctly ran on testnet."""
    sentinel_exchange = object()

    agent = MarketMonitorAgent(symbols=["BTC/USDT"], exchange=sentinel_exchange)

    assert agent._feeds["BTC/USDT"]._exchange is sentinel_exchange


def test_update_symbols_passes_through_the_injected_exchange_for_new_symbols():
    sentinel_exchange = object()
    agent = MarketMonitorAgent(symbols=["BTC/USDT"], exchange=sentinel_exchange)

    agent.update_symbols(["BTC/USDT", "ETH/USDT"])

    assert agent._feeds["ETH/USDT"]._exchange is sentinel_exchange


class StructurallyBrokenFeed:
    """A frame with an impossible OHLC relationship (high < low) - the kind
    of garbage a real exchange glitch could return without ever raising an
    exception or returning an empty frame."""

    def fetch_ohlcv(self, limit=100):
        n = 40
        closes = [100.0 + i for i in range(n)]
        frame = pd.DataFrame(
            {
                "timestamp": pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC"),
                "open": closes,
                "high": [c + 1 for c in closes],
                "low": [c - 1 for c in closes],
                "close": closes,
                "volume": [100] * n,
            }
        )
        frame.loc[frame.index[-1], "high"] = 50.0
        frame.loc[frame.index[-1], "low"] = 60.0  # high < low on the latest bar
        return frame


def test_integrity_checks_disabled_by_default():
    """Backward compatible: existing callers get exactly the old behavior
    (no integrity checking at all) unless they opt in."""
    agent = MarketMonitorAgent(symbols=["BTC/USDT"], feeds={"BTC/USDT": StructurallyBrokenFeed()})

    snapshots = agent.poll()

    assert "BTC/USDT" in snapshots
    assert agent.failed_symbols == []


def test_structurally_broken_data_skipped_when_integrity_checks_enabled():
    agent = MarketMonitorAgent(
        symbols=["BTC/USDT"], feeds={"BTC/USDT": StructurallyBrokenFeed()}, enable_integrity_checks=True
    )

    snapshots = agent.poll()

    assert "BTC/USDT" not in snapshots
    assert "BTC/USDT" in agent.failed_symbols


def test_integrity_failure_raises_an_alert_when_configured(tmp_path):
    from trading_assistant.agents.alerting import AlertManager
    from trading_assistant.persistence.journal import Journal

    journal = Journal(db_path=str(tmp_path / "journal.db"))
    alert_manager = AlertManager(journal=journal)
    agent = MarketMonitorAgent(
        symbols=["BTC/USDT"],
        feeds={"BTC/USDT": StructurallyBrokenFeed()},
        enable_integrity_checks=True,
        alert_manager=alert_manager,
    )

    agent.poll()

    alerts = journal.recent_alerts()
    assert len(alerts) == 1
    assert alerts[0]["category"] == "market_data_integrity"
    assert "BTC/USDT" in alerts[0]["message"]


def test_extreme_move_threshold_respected_when_enabled():
    closes = [100.0] * 39 + [500.0]  # last bar jumps 400%
    frame_feed = FakeFeed(closes)
    agent = MarketMonitorAgent(
        symbols=["BTC/USDT"],
        feeds={"BTC/USDT": frame_feed},
        enable_integrity_checks=True,
        max_single_bar_move_pct=0.5,
    )

    snapshots = agent.poll()

    assert "BTC/USDT" not in snapshots
    assert "BTC/USDT" in agent.failed_symbols


def test_clean_data_passes_integrity_checks():
    closes = [float(i) for i in range(100, 141)]  # steady, no anomalies
    agent = MarketMonitorAgent(
        symbols=["BTC/USDT"],
        feeds={"BTC/USDT": FakeFeed(closes)},
        enable_integrity_checks=True,
        max_single_bar_move_pct=0.5,
    )

    snapshots = agent.poll()

    assert "BTC/USDT" in snapshots
    assert agent.failed_symbols == []


def _snapshot_with_last_bar_conditions(monkeypatch, atr_pct, relative_volume):
    closes = [100.0 + i for i in range(30)]

    def fake_build_features(frame, *args, **kwargs):
        result = frame.copy()
        result["sma_fast_vs_slow"] = [0.05] * 30
        result["adx_strength"] = [0.60] * 30
        result["rsi_normalized"] = [0.5] * 30
        result["atr_pct"] = [0.01] * 29 + [atr_pct]
        result["relative_volume"] = [1.0] * 29 + [relative_volume]
        return result

    monkeypatch.setattr("trading_assistant.agents.market_monitor.build_features", fake_build_features)

    agent = MarketMonitorAgent(symbols=["BTC/USDT"], feeds={"BTC/USDT": FakeFeed(closes)})
    return agent.poll()["BTC/USDT"]


def test_market_condition_is_none_under_normal_conditions(monkeypatch):
    snapshot = _snapshot_with_last_bar_conditions(monkeypatch, atr_pct=0.01, relative_volume=1.0)

    assert snapshot.market_condition is None


def test_market_condition_flags_panic_on_extreme_atr(monkeypatch):
    snapshot = _snapshot_with_last_bar_conditions(monkeypatch, atr_pct=0.15, relative_volume=1.0)

    assert snapshot.market_condition == "panic"


def test_market_condition_flags_illiquid_on_thin_volume(monkeypatch):
    snapshot = _snapshot_with_last_bar_conditions(monkeypatch, atr_pct=0.01, relative_volume=0.05)

    assert snapshot.market_condition == "illiquid"


def test_market_condition_uses_latest_bar_not_a_windowed_average(monkeypatch):
    """Unlike regime (windowed), market_condition must react to the CURRENT
    bar immediately - a panic spike averaged away over regime_window_bars
    would defeat the purpose of catching it while it's happening."""
    closes = [100.0 + i for i in range(120)]

    def fake_build_features(frame, *args, **kwargs):
        result = frame.copy()
        n = len(result)
        result["sma_fast_vs_slow"] = [0.05] * n
        result["adx_strength"] = [0.60] * n
        result["rsi_normalized"] = [0.5] * n
        result["atr_pct"] = [0.01] * (n - 1) + [0.20]  # only the very last bar spikes
        result["relative_volume"] = [1.0] * n
        return result

    monkeypatch.setattr("trading_assistant.agents.market_monitor.build_features", fake_build_features)

    agent = MarketMonitorAgent(symbols=["BTC/USDT"], regime_window_bars=100, feeds={"BTC/USDT": FakeFeed(closes)})

    snapshot = agent.poll()["BTC/USDT"]

    assert snapshot.market_condition == "panic"


class StillFormingCandleFeed:
    """Like FakeFeed, but the LAST bar's timestamp is real 'now' minus a
    few seconds - i.e. still within the current 1h interval, matching
    ccxt's actual behavior of always including the in-progress candle as
    the last row of a live fetch_ohlcv response."""

    def __init__(self, closes):
        self.closes = closes

    def fetch_ohlcv(self, limit=100):
        n = len(self.closes)
        now = pd.Timestamp.now(tz="UTC")
        # every prior bar closed a full hour apart, ending just before now
        timestamps = [now.floor("h") - pd.Timedelta(hours=(n - 1 - i)) for i in range(n)]
        timestamps[-1] = now - pd.Timedelta(seconds=10)  # last bar started 10s ago - still forming
        return pd.DataFrame(
            {
                "timestamp": timestamps,
                "open": self.closes,
                "high": [c + 1 for c in self.closes],
                "low": [c - 1 for c in self.closes],
                "close": self.closes,
                "volume": [100] * n,
            }
        )


def test_market_condition_ignores_a_still_forming_last_bar(monkeypatch):
    """Regression: ccxt's fetch_ohlcv always includes the currently-forming
    candle as the last row - with the default 1h timeframe and a 300s poll
    interval (config.yaml's actual live settings), most polls land
    partway through a fresh hourly bar. relative_volume for that bar is
    then trivially small purely because little wall-clock time has
    elapsed, not because the market is thin - market_condition used to
    read that partial bar directly and falsely flag "illiquid" on a
    perfectly liquid symbol."""
    closes = [100.0 + i for i in range(30)]

    def fake_build_features(frame, *args, **kwargs):
        result = frame.copy()
        n = len(result)
        result["sma_fast_vs_slow"] = [0.05] * n
        result["adx_strength"] = [0.60] * n
        result["rsi_normalized"] = [0.5] * n
        result["atr_pct"] = [0.01] * n
        # the still-forming last bar reads as falsely thin; the prior
        # (closed) bar is genuinely normal volume
        result["relative_volume"] = [1.0] * (n - 1) + [0.05]
        return result

    monkeypatch.setattr("trading_assistant.agents.market_monitor.build_features", fake_build_features)

    agent = MarketMonitorAgent(
        symbols=["BTC/USDT"], timeframe="1h", feeds={"BTC/USDT": StillFormingCandleFeed(closes)}
    )

    snapshot = agent.poll()["BTC/USDT"]

    assert snapshot.market_condition is None  # must use the last CLOSED bar, not the forming one


def test_market_condition_still_uses_a_genuinely_closed_last_bar(monkeypatch):
    """The fix must not blind the check to a real condition on a bar that
    genuinely has closed - only an in-progress bar should be skipped."""
    closes = [100.0 + i for i in range(30)]

    def fake_build_features(frame, *args, **kwargs):
        result = frame.copy()
        n = len(result)
        result["sma_fast_vs_slow"] = [0.05] * n
        result["adx_strength"] = [0.60] * n
        result["rsi_normalized"] = [0.5] * n
        result["atr_pct"] = [0.01] * n
        result["relative_volume"] = [1.0] * (n - 1) + [0.05]
        return result

    monkeypatch.setattr("trading_assistant.agents.market_monitor.build_features", fake_build_features)

    # FakeFeed's fixed 2026-01-01 timestamps are always long closed
    agent = MarketMonitorAgent(symbols=["BTC/USDT"], timeframe="1h", feeds={"BTC/USDT": FakeFeed(closes)})

    snapshot = agent.poll()["BTC/USDT"]

    assert snapshot.market_condition == "illiquid"


def test_compute_regime_churn_rate_pure_function():
    assert MarketMonitorAgent.compute_regime_churn_rate([]) is None
    assert MarketMonitorAgent.compute_regime_churn_rate(["trending_up"]) is None
    assert MarketMonitorAgent.compute_regime_churn_rate(["trending_up", "trending_up", "trending_up"]) == 0.0
    assert MarketMonitorAgent.compute_regime_churn_rate(["trending_up", "trending_down"]) == 1.0
    assert MarketMonitorAgent.compute_regime_churn_rate(
        ["trending_up", "trending_down", "trending_down", "ranging"]
    ) == pytest.approx(2 / 3)


def test_regime_churn_rate_is_none_before_two_polls():
    closes = [float(i) for i in range(1, 41)]
    agent = MarketMonitorAgent(symbols=["BTC/USDT"], feeds={"BTC/USDT": FakeFeed(closes)})

    agent.poll()

    assert agent.regime_churn_rate("BTC/USDT") is None


def test_regime_churn_rate_tracks_repeated_polls(monkeypatch):
    closes = [float(i) for i in range(1, 41)]
    agent = MarketMonitorAgent(
        symbols=["BTC/USDT"], feeds={"BTC/USDT": FakeFeed(closes)}, regime_history_window=4
    )
    regimes = iter(["trending_up", "trending_down", "trending_down", "ranging"])
    monkeypatch.setattr(
        "trading_assistant.agents.market_monitor.classify_regime", lambda *args, **kwargs: next(regimes)
    )

    for _ in range(4):
        agent.poll()

    assert agent.regime_churn_rate("BTC/USDT") == pytest.approx(2 / 3)


def test_regime_history_window_bounds_stored_history(monkeypatch):
    closes = [float(i) for i in range(1, 41)]
    agent = MarketMonitorAgent(
        symbols=["BTC/USDT"], feeds={"BTC/USDT": FakeFeed(closes)}, regime_history_window=3
    )
    monkeypatch.setattr(
        "trading_assistant.agents.market_monitor.classify_regime", lambda *args, **kwargs: "trending_up"
    )

    for _ in range(10):
        agent.poll()

    assert len(agent._regime_history["BTC/USDT"]) == 3


def test_regime_churn_alert_fires_when_threshold_exceeded(tmp_path, monkeypatch):
    from trading_assistant.agents.alerting import AlertManager
    from trading_assistant.persistence.journal import Journal

    closes = [float(i) for i in range(1, 41)]
    journal = Journal(db_path=str(tmp_path / "journal.db"))
    alert_manager = AlertManager(journal=journal)
    agent = MarketMonitorAgent(
        symbols=["BTC/USDT"],
        feeds={"BTC/USDT": FakeFeed(closes)},
        regime_history_window=4,
        regime_churn_alert_threshold=0.5,
        alert_manager=alert_manager,
    )
    regimes = itertools.cycle(["trending_up", "trending_down"])
    monkeypatch.setattr(
        "trading_assistant.agents.market_monitor.classify_regime", lambda *args, **kwargs: next(regimes)
    )

    for _ in range(4):
        agent.poll()

    alerts = journal.recent_alerts()
    assert len(alerts) == 1
    assert alerts[0]["category"] == "regime_churn"
    assert "BTC/USDT" in alerts[0]["message"]


def test_regime_churn_alert_disabled_by_default(monkeypatch):
    """Backward compatible: no alert threshold configured -> no alerts even
    when the regime is flapping every single poll."""
    closes = [float(i) for i in range(1, 41)]
    agent = MarketMonitorAgent(
        symbols=["BTC/USDT"], feeds={"BTC/USDT": FakeFeed(closes)}, regime_history_window=4
    )
    regimes = itertools.cycle(["trending_up", "trending_down"])
    monkeypatch.setattr(
        "trading_assistant.agents.market_monitor.classify_regime", lambda *args, **kwargs: next(regimes)
    )

    for _ in range(4):
        agent.poll()

    assert agent.regime_churn_rate("BTC/USDT") == 1.0
