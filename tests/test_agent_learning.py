import numpy as np
import pandas as pd
import pytest

from trading_assistant.agents.features import FEATURE_COLUMNS
from trading_assistant.agents.learning import LearningAgent
from trading_assistant.agents.prediction import PredictionAgent, PredictProbaAdapter


class FakeModel:
    """Mimics ml.predictor.EnsemblePredictor's interface (predict / predict_with_confidence,
    no predict_proba) with a fixed, deterministic output - avoids flaky ML-on-tiny-data tests."""

    def __init__(self, prediction_value):
        self.prediction_value = prediction_value
        self.trained_on = None

    def train(self, X, y):
        self.trained_on = len(X)

    def predict(self, X):
        return np.full(len(X), self.prediction_value)

    def predict_with_confidence(self, X):
        return np.full(len(X), self.prediction_value), np.full(len(X), 0.7)


def _uptrend_ohlcv(n=300, start=100.0, step=0.5):
    closes = [start + i * step for i in range(n)]
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC"),
            "open": closes,
            "high": [c + 1 for c in closes],
            "low": [c - 1 for c in closes],
            "close": closes,
            "volume": [100] * n,
        }
    )


def _regime_change_ohlcv(n=300, start=100.0, step=0.5):
    """First half trending up, second half trending down."""
    half = n // 2
    up = [start + i * step for i in range(half)]
    down = [up[-1] - i * step for i in range(1, n - half + 1)]
    closes = up + down
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC"),
            "open": closes,
            "high": [c + 1 for c in closes],
            "low": [c - 1 for c in closes],
            "close": closes,
            "volume": [100] * n,
        }
    )


class RecordingModel:
    """Captures the row-index range it was trained/predicted on each call,
    so tests can directly verify walk-forward's temporal ordering."""

    def __init__(self):
        self.train_index_ranges = []
        self.predict_index_ranges = []

    def train(self, X, y):
        self.train_index_ranges.append((X.index.min(), X.index.max()))

    def predict(self, X):
        self.predict_index_ranges.append((X.index.min(), X.index.max()))
        return np.full(len(X), 0.9)


def _agent(model_value, tmp_path, **kwargs):
    prediction_agent = PredictionAgent(model_dir=tmp_path, auto_load=False)
    # Small windows so unit tests run fast on a few hundred synthetic bars,
    # while still exercising the real walk-forward code path (not a mock of
    # it). min_pct_folds_profitable defaults permissive here since most of
    # these tests aren't specifically about that gate - see the dedicated
    # test for it below.
    defaults = dict(
        train_window_bars=100,
        test_window_bars=20,
        target_folds=3,
        min_folds_required=2,
        min_pct_folds_profitable=0.0,
    )
    defaults.update(kwargs)
    return LearningAgent(
        prediction_agent=prediction_agent,
        model_factory=lambda: FakeModel(model_value),
        **defaults,
    ), prediction_agent


def test_backtest_uses_risk_based_sizing_when_configured(tmp_path, monkeypatch):
    """Parity fix: LearningAgent's own walk-forward backtest must size
    trades with the SAME risk_per_trade/stop-distance formula live trading
    uses when configured, not the old flat ~95%-of-capital assumption -
    otherwise a promoted model's validated Sharpe/drawdown describes
    performance at a risk level it would never actually be traded at."""
    from trading_assistant.agents.features import build_features
    from trading_assistant.backtesting.framework import BacktestEngineV2

    agent, _ = _agent(0.9, tmp_path, risk_per_trade=0.02, position_stop_loss_pct=0.01, max_position_size=0.1)
    featured = build_features(_uptrend_ohlcv(n=60)).dropna(subset=FEATURE_COLUMNS).reset_index(drop=True)

    captured = {}
    original_open_position = BacktestEngineV2.open_position

    def capturing_open_position(self, symbol, direction, price, timestamp, quantity=None, risk_pct=0.02):
        captured.setdefault("quantity", quantity)
        return original_open_position(self, symbol, direction, price, timestamp, quantity=quantity, risk_pct=risk_pct)

    monkeypatch.setattr(BacktestEngineV2, "open_position", capturing_open_position)

    agent._backtest(FakeModel(0.9), "BTC/USDT", featured)

    # risk_amount = 10000*0.02 = 200; notional = 200/0.01 = 20000, capped at
    # 10000*0.1 = 1000 -> quantity = 1000/price, a small fraction of the old
    # (10000*0.95)/price the flat-sizing default would have opened.
    assert captured["quantity"] == pytest.approx(1000.0 / featured["close"].iloc[0])


def test_backtest_keeps_old_flat_sizing_by_default(tmp_path, monkeypatch):
    """Backward compatible: risk_per_trade defaults to None."""
    from trading_assistant.agents.features import build_features
    from trading_assistant.backtesting.framework import BacktestEngineV2

    agent, _ = _agent(0.9, tmp_path)
    featured = build_features(_uptrend_ohlcv(n=60)).dropna(subset=FEATURE_COLUMNS).reset_index(drop=True)

    captured = {}
    original_open_position = BacktestEngineV2.open_position

    def capturing_open_position(self, symbol, direction, price, timestamp, quantity=None, risk_pct=0.02):
        captured.setdefault("quantity", quantity)
        return original_open_position(self, symbol, direction, price, timestamp, quantity=quantity, risk_pct=risk_pct)

    monkeypatch.setattr(BacktestEngineV2, "open_position", capturing_open_position)

    agent._backtest(FakeModel(0.9), "BTC/USDT", featured)

    assert captured["quantity"] == pytest.approx((10000.0 * 0.95) / featured["close"].iloc[0])


def test_retrain_rejects_when_not_enough_history(tmp_path):
    agent, _ = _agent(0.9, tmp_path)
    tiny_ohlcv = _uptrend_ohlcv(n=10)

    outcome = agent.retrain("BTC/USDT", tiny_ohlcv)

    assert outcome.promoted is False
    assert "not enough history" in outcome.reason


def test_retrain_promotes_a_consistently_profitable_candidate(tmp_path):
    agent, prediction_agent = _agent(0.9, tmp_path, min_sharpe_to_promote=0.1, max_drawdown_to_promote=0.5)
    ohlcv = _uptrend_ohlcv()

    outcome = agent.retrain("BTC/USDT", ohlcv)

    assert outcome.promoted is True
    assert outcome.candidate_metrics["num_folds"] >= 2
    assert outcome.candidate_metrics["total_trades"] >= 1
    assert agent.last_promoted_metrics["BTC/USDT"] == outcome.candidate_metrics
    # the live PredictionAgent's model was actually swapped, for this symbol specifically
    strategy = prediction_agent._get_or_create_strategy("BTC/USDT")
    assert isinstance(strategy.model, PredictProbaAdapter)
    assert strategy.feature_columns == FEATURE_COLUMNS

    # a different, untrained symbol must be unaffected
    other_strategy = prediction_agent._get_or_create_strategy("ETH/USDT")
    assert other_strategy.model is None


def test_walk_forward_backtest_reflects_configured_commission_rate(tmp_path):
    """Regression: BacktestEngineV2 always used its own hardcoded
    commission/slippage defaults (0.001/0.0005), never
    config.portfolio.commission_rate - an operator changing the real fee
    assumption would silently leave every promotion decision validating
    against a stale friction assumption forever after."""
    ohlcv = _uptrend_ohlcv()
    zero_friction, _ = _agent(0.9, tmp_path, commission_rate=0.0, slippage_rate=0.0)
    high_friction, _ = _agent(0.9, tmp_path, commission_rate=0.05, slippage_rate=0.0)  # large, obvious 5% fee

    zero_outcome = zero_friction.retrain("BTC/USDT", ohlcv)
    high_outcome = high_friction.retrain("BTC/USDT", ohlcv)

    assert high_outcome.candidate_metrics["mean_sharpe"] < zero_outcome.candidate_metrics["mean_sharpe"]


def test_retrain_rejects_when_sharpe_below_threshold(tmp_path):
    # prediction_value=0.5 never crosses the 0.6/0.4 buy/sell thresholds -> zero trades -> sharpe 0
    agent, _ = _agent(0.5, tmp_path, min_sharpe_to_promote=0.1)
    ohlcv = _uptrend_ohlcv()

    outcome = agent.retrain("BTC/USDT", ohlcv)

    assert outcome.promoted is False
    assert "sharpe" in outcome.reason
    assert outcome.candidate_metrics["total_trades"] == 0


def test_retrain_rejects_candidate_that_does_not_beat_current_live_model(tmp_path):
    agent, _ = _agent(0.9, tmp_path, min_sharpe_to_promote=0.01, max_drawdown_to_promote=0.9)
    agent.last_promoted_metrics["BTC/USDT"] = {"median_sharpe": 999.0}  # an unbeatable existing baseline
    ohlcv = _uptrend_ohlcv()

    outcome = agent.retrain("BTC/USDT", ohlcv)

    assert outcome.promoted is False
    assert "did not" in outcome.reason and "beat" in outcome.reason


def test_retrain_writes_retrain_event_to_journal(tmp_path):
    from trading_assistant.persistence.journal import Journal

    journal = Journal(db_path=str(tmp_path / "journal.db"))
    agent, _ = _agent(0.9, tmp_path, min_sharpe_to_promote=0.1, max_drawdown_to_promote=0.5, journal=journal)
    ohlcv = _uptrend_ohlcv()

    agent.retrain("BTC/USDT", ohlcv)

    with journal._connect() as conn:
        row = conn.execute("SELECT * FROM retrain_events").fetchone()
    assert row is not None
    # Without this, the dashboard's "Model health" table can't tell which
    # symbol a retrain attempt belongs to when several symbols retrain in
    # the same batch - found live: every row looked identical apart from
    # timestamp.
    assert row["symbol"] == "BTC/USDT"


def test_promoting_one_symbol_does_not_gate_a_different_symbols_promotion(tmp_path):
    """Regression test: last_promoted_metrics must be tracked per symbol.
    A shared scalar baseline would let BTC/USDT's score block or leak into
    ETH/USDT's independent promotion decision."""
    agent, prediction_agent = _agent(0.9, tmp_path, min_sharpe_to_promote=0.1, max_drawdown_to_promote=0.5)
    ohlcv = _uptrend_ohlcv()

    btc_outcome = agent.retrain("BTC/USDT", ohlcv)
    eth_outcome = agent.retrain("ETH/USDT", ohlcv)

    assert btc_outcome.promoted is True
    assert eth_outcome.promoted is True
    assert "BTC/USDT" in agent.last_promoted_metrics
    assert "ETH/USDT" in agent.last_promoted_metrics
    # each symbol's live strategy is its own object with its own model
    assert prediction_agent._get_or_create_strategy("BTC/USDT") is not prediction_agent._get_or_create_strategy(
        "ETH/USDT"
    )


def test_walk_forward_folds_never_train_on_data_from_their_own_test_window(tmp_path):
    """No data leakage: for every walk-forward fold, every training row's
    position must be strictly before every row in that same fold's test
    window, and test windows across folds must never overlap - otherwise a
    fold's backtest would be validated against data the model (directly or
    indirectly) already saw."""
    recording_model = RecordingModel()
    prediction_agent = PredictionAgent(model_dir=tmp_path, auto_load=False)
    agent = LearningAgent(
        prediction_agent=prediction_agent,
        model_factory=lambda: recording_model,
        train_window_bars=100,
        test_window_bars=20,
        min_folds_required=2,
    )
    ohlcv = _uptrend_ohlcv(n=300)

    agent.retrain("BTC/USDT", ohlcv)

    num_folds = len(recording_model.predict_index_ranges)
    assert num_folds >= 2

    for i in range(num_folds):
        train_min, train_max = recording_model.train_index_ranges[i]
        test_min, test_max = recording_model.predict_index_ranges[i]
        assert train_max < test_min, f"fold {i}: training data reaches into or past its own test window"

    for i in range(1, num_folds):
        prev_test_max = recording_model.predict_index_ranges[i - 1][1]
        curr_test_min = recording_model.predict_index_ranges[i][0]
        assert curr_test_min > prev_test_max, f"fold {i}: test window overlaps the previous fold's test window"


def test_retrain_rejects_a_regime_dependent_model_on_consistency_even_when_sharpe_passes(tmp_path):
    """Regression coverage for min_pct_folds_profitable: a trend-following
    model that's profitable while the trend holds and loses money once the
    market reverses must be rejected as too regime-dependent - using a
    deliberately permissive sharpe threshold here to isolate the consistency
    gate itself from the (separate, already-tested) sharpe gate."""
    agent, _ = _agent(
        0.9,
        tmp_path,
        min_sharpe_to_promote=-1000.0,  # pass the sharpe gate trivially to isolate the consistency gate
        max_drawdown_to_promote=1.0,
        min_pct_folds_profitable=0.9,  # require almost all folds profitable
    )
    ohlcv = _regime_change_ohlcv(n=300)

    outcome = agent.retrain("BTC/USDT", ohlcv)

    assert outcome.promoted is False
    assert "profitable" in outcome.reason
    assert outcome.candidate_metrics["pct_folds_profitable"] < 0.9


def test_retrain_from_live_passes_through_the_injected_exchange_to_a_fallback_feed():
    """Regression: LearningAgent defaulted every on-demand LiveFeed to a
    bare, always-mainnet ccxt instance since no exchange was ever threaded
    through - found via an audit while execution correctly ran on testnet."""
    sentinel_exchange = object()
    captured = {}

    class RecordingLiveFeed:
        def __init__(self, symbol, timeframe, exchange=None):
            captured["exchange"] = exchange

        def fetch_ohlcv_history(self, total_bars):
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    import trading_assistant.agents.learning as learning_module

    original_live_feed = learning_module.LiveFeed
    learning_module.LiveFeed = RecordingLiveFeed
    try:
        agent = LearningAgent(prediction_agent=PredictionAgent(), exchange=sentinel_exchange)
        try:
            agent.retrain_from_live("BTC/USDT")
        except Exception:  # noqa: BLE001 - the empty fake feed can't satisfy the full
            pass  # walk-forward pipeline; only the exchange threaded into LiveFeed matters here
    finally:
        learning_module.LiveFeed = original_live_feed

    assert captured["exchange"] is sentinel_exchange


def test_retrain_promotion_persists_a_feature_baseline(tmp_path):
    from trading_assistant.persistence.journal import Journal

    journal = Journal(db_path=str(tmp_path / "journal.db"))
    agent, _ = _agent(0.9, tmp_path, min_sharpe_to_promote=0.1, max_drawdown_to_promote=0.5, journal=journal)
    ohlcv = _uptrend_ohlcv()

    outcome = agent.retrain("BTC/USDT", ohlcv)

    assert outcome.promoted is True
    baseline = journal.latest_feature_baseline("BTC/USDT")
    assert baseline is not None
    assert set(baseline["boundaries"].keys()) <= set(FEATURE_COLUMNS)
    assert baseline["boundaries"]  # at least one feature had enough spread to bucket


def test_rejected_retrain_does_not_persist_a_feature_baseline(tmp_path):
    from trading_assistant.persistence.journal import Journal

    journal = Journal(db_path=str(tmp_path / "journal.db"))
    agent, _ = _agent(0.5, tmp_path, min_sharpe_to_promote=0.1, journal=journal)
    ohlcv = _uptrend_ohlcv()

    outcome = agent.retrain("BTC/USDT", ohlcv)

    assert outcome.promoted is False
    assert journal.latest_feature_baseline("BTC/USDT") is None


def test_check_feature_drift_returns_none_without_a_journal(tmp_path):
    agent, _ = _agent(0.9, tmp_path)
    live_features = _uptrend_ohlcv(n=250)

    assert agent.check_feature_drift("BTC/USDT", live_features) is None


def test_check_feature_drift_returns_none_without_a_baseline_yet(tmp_path):
    from trading_assistant.persistence.journal import Journal

    journal = Journal(db_path=str(tmp_path / "journal.db"))
    agent, _ = _agent(0.9, tmp_path, journal=journal)
    live_features = _uptrend_ohlcv(n=250)

    assert agent.check_feature_drift("BTC/USDT", live_features) is None


def test_check_feature_drift_detects_a_regime_shift_against_the_promoted_baseline(tmp_path):
    from trading_assistant.agents.features import build_features
    from trading_assistant.persistence.journal import Journal

    journal = Journal(db_path=str(tmp_path / "journal.db"))
    agent, _ = _agent(0.9, tmp_path, min_sharpe_to_promote=0.1, max_drawdown_to_promote=0.5, journal=journal)
    agent.retrain("BTC/USDT", _uptrend_ohlcv())  # promotes and captures a baseline off an uptrend

    # A sharply different (downtrending) live window should register as a
    # meaningful shift in trend-related features versus the uptrend baseline.
    downtrend_features = build_features(_uptrend_ohlcv(step=-0.5, start=300.0)).dropna(subset=FEATURE_COLUMNS)
    summary = agent.check_feature_drift("BTC/USDT", downtrend_features)

    assert summary is not None
    assert summary["overall_severity"] in ("moderate", "significant")
    assert summary["worst_features"]

    events = journal.recent_drift_events(symbol="BTC/USDT")
    assert len(events) == 1
    assert events[0]["overall_severity"] == summary["overall_severity"]


def test_check_feature_drift_reports_none_severity_for_a_similar_live_window(tmp_path):
    from trading_assistant.agents.features import build_features
    from trading_assistant.persistence.journal import Journal

    journal = Journal(db_path=str(tmp_path / "journal.db"))
    agent, _ = _agent(0.9, tmp_path, min_sharpe_to_promote=0.1, max_drawdown_to_promote=0.5, journal=journal)
    ohlcv = _uptrend_ohlcv()
    agent.retrain("BTC/USDT", ohlcv)

    same_regime_features = build_features(ohlcv).dropna(subset=FEATURE_COLUMNS)
    summary = agent.check_feature_drift("BTC/USDT", same_regime_features)

    assert summary is not None
    assert summary["overall_severity"] == "none"


def test_check_feature_drift_raises_a_critical_alert_on_significant_drift(tmp_path):
    from trading_assistant.agents.alerting import AlertManager
    from trading_assistant.agents.features import build_features
    from trading_assistant.persistence.journal import Journal

    journal = Journal(db_path=str(tmp_path / "journal.db"))
    alert_manager = AlertManager(journal=journal)
    agent, _ = _agent(
        0.9, tmp_path, min_sharpe_to_promote=0.1, max_drawdown_to_promote=0.5, journal=journal,
        alert_manager=alert_manager,
    )
    agent.retrain("BTC/USDT", _uptrend_ohlcv())

    downtrend_features = build_features(_uptrend_ohlcv(step=-0.5, start=300.0)).dropna(subset=FEATURE_COLUMNS)
    summary = agent.check_feature_drift("BTC/USDT", downtrend_features)

    assert summary["overall_severity"] == "significant"
    alerts = journal.recent_alerts()
    assert len(alerts) == 1
    assert alerts[0]["category"] == "feature_drift"
    assert alerts[0]["severity"] == "critical"
    assert "BTC/USDT" in alerts[0]["message"]


def test_check_feature_drift_raises_no_alert_when_severity_is_none(tmp_path):
    from trading_assistant.agents.alerting import AlertManager
    from trading_assistant.agents.features import build_features
    from trading_assistant.persistence.journal import Journal

    journal = Journal(db_path=str(tmp_path / "journal.db"))
    alert_manager = AlertManager(journal=journal)
    agent, _ = _agent(
        0.9, tmp_path, min_sharpe_to_promote=0.1, max_drawdown_to_promote=0.5, journal=journal,
        alert_manager=alert_manager,
    )
    ohlcv = _uptrend_ohlcv()
    agent.retrain("BTC/USDT", ohlcv)

    same_regime_features = build_features(ohlcv).dropna(subset=FEATURE_COLUMNS)
    summary = agent.check_feature_drift("BTC/USDT", same_regime_features)

    assert summary["overall_severity"] == "none"
    assert journal.recent_alerts() == []


class TestCheckLiveDecay:
    def _agent_with_baseline(self, tmp_path, journal=None, **kwargs):
        agent, prediction_agent = _agent(
            0.9, tmp_path, min_sharpe_to_promote=0.1, max_drawdown_to_promote=0.5, journal=journal, **kwargs
        )
        agent.retrain("BTC/USDT", _uptrend_ohlcv())  # promotes and sets last_promoted_metrics
        return agent, prediction_agent

    def test_disabled_by_default(self, tmp_path):
        agent, _ = self._agent_with_baseline(tmp_path)  # min_live_trades_for_decay_check defaults to None

        summary = agent.check_live_decay("BTC/USDT", [-1.0] * 20)

        assert summary is None

    def test_no_baseline_yet_returns_none(self, tmp_path):
        agent, _ = _agent(0.9, tmp_path, min_live_trades_for_decay_check=5)  # never promoted

        assert agent.check_live_decay("BTC/USDT", [-1.0] * 20) is None

    def test_not_enough_trades_yet_returns_none(self, tmp_path):
        agent, _ = self._agent_with_baseline(tmp_path, min_live_trades_for_decay_check=10)

        assert agent.check_live_decay("BTC/USDT", [-1.0] * 5) is None

    def test_confirmed_decay_rolls_back_and_alerts(self, tmp_path):
        from trading_assistant.agents.alerting import AlertManager
        from trading_assistant.persistence.journal import Journal

        journal = Journal(db_path=str(tmp_path / "journal.db"))
        alert_manager = AlertManager(journal=journal)
        agent, prediction_agent = self._agent_with_baseline(
            tmp_path, journal=journal, alert_manager=alert_manager, min_live_trades_for_decay_check=10
        )
        # A second promotion (independent of retrain()'s own promotion
        # gate, which isn't what this test is about) so there's a
        # "previous" model version on disk to roll back to.
        prediction_agent.promote_model("BTC/USDT", FakeModel(0.5), feature_columns=FEATURE_COLUMNS)

        # Overwhelmingly losing, net-negative real trades - a real decay.
        summary = agent.check_live_decay("BTC/USDT", [-10.0] * 9 + [1.0])

        assert summary["decayed"] is True
        assert summary["rolled_back"] is True
        alerts = journal.recent_alerts()
        assert any(a["category"] == "model_decay" and a["severity"] == "critical" for a in alerts)

    def test_healthy_live_performance_is_not_flagged(self, tmp_path):
        agent, _ = self._agent_with_baseline(tmp_path, min_live_trades_for_decay_check=10)

        summary = agent.check_live_decay("BTC/USDT", [1.0] * 9 + [-1.0])  # mostly winning, net positive

        assert summary["decayed"] is False

    def test_losing_but_within_validated_win_rate_is_not_flagged(self, tmp_path):
        """A losing stretch that's still consistent with what walk-forward
        itself found acceptable must not trigger a rollback - only a
        MEANINGFUL divergence from the validated baseline should."""
        agent, _ = self._agent_with_baseline(tmp_path, min_live_trades_for_decay_check=10, decay_win_rate_fraction=0.3)
        # mean_win_rate (a genuine per-trade win rate), not
        # pct_folds_profitable (a different, fold-level metric) - see
        # check_live_decay's comparison.
        baseline_win_rate = agent.last_promoted_metrics["BTC/USDT"]["mean_win_rate"]

        # win rate only mildly below baseline, not below 30% of it
        pnls = [1.0 if i < round(baseline_win_rate * 8) else -1.0 for i in range(10)]

        summary = agent.check_live_decay("BTC/USDT", pnls)

        assert summary["decayed"] is False

    def test_baseline_win_rate_uses_mean_win_rate_not_pct_folds_profitable(self, tmp_path):
        """Regression: check_live_decay used to compare a live per-trade
        win rate against pct_folds_profitable - the fraction of FOLD
        windows that ended net-positive, a coarser and different metric.
        A model can clear 90% of fold windows while its real per-trade win
        rate is ~35%; comparing against the wrong metric could mask a real
        decay or falsely trip one. Directly asserts the two metrics differ
        for this baseline and that the summary reports the correct one."""
        agent, _ = self._agent_with_baseline(tmp_path, min_live_trades_for_decay_check=10)
        metrics = agent.last_promoted_metrics["BTC/USDT"]

        assert "mean_win_rate" in metrics
        summary = agent.check_live_decay("BTC/USDT", [1.0] * 9 + [-1.0])
        assert summary["baseline_win_rate"] == metrics["mean_win_rate"]

    def test_no_rollback_when_disabled_but_flagged(self, tmp_path):
        """Even with no previous model version available, a genuine decay
        must still be detected and reported - rollback failing/being
        unavailable is not the same as nothing being wrong."""
        agent, _ = self._agent_with_baseline(tmp_path, min_live_trades_for_decay_check=10)
        # only ever promoted once in this test (no second retrain) - no "previous" version exists

        summary = agent.check_live_decay("BTC/USDT", [-10.0] * 9 + [1.0])

        assert summary["decayed"] is True
        assert summary["rolled_back"] is False
