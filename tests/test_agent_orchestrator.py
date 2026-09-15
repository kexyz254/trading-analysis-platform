import asyncio
import types

import numpy as np
import pandas as pd
import pytest

from trading_assistant.agents.execution import ExecutionAgent
from trading_assistant.agents.learning import LearningAgent
from trading_assistant.agents.market_monitor import MarketMonitorAgent
from trading_assistant.agents.orchestrator import Orchestrator
from trading_assistant.agents.portfolio import PortfolioAgent
from trading_assistant.agents.prediction import PredictionAgent, PredictionResult
from trading_assistant.agents.risk import RiskAgent
from trading_assistant.persistence.journal import Journal
from trading_assistant.portfolio.manager import PortfolioManager
from trading_assistant.strategies.base import Signal


class FakeFeed:
    def __init__(self, closes):
        self.closes = closes

    def fetch_ohlcv(self, limit=100):
        n = len(self.closes)
        # High/low as a fixed FRACTION of price (not a fixed absolute unit)
        # so ATR% stays realistic regardless of the price level a given
        # test uses - many tests here use small integer closes (1..20)
        # where a fixed +-1 absolute offset would imply an implausibly
        # huge relative bar range and falsely trip the panic guard.
        return pd.DataFrame(
            {
                "timestamp": pd.date_range("2026-01-01", periods=n, freq="min", tz="UTC"),
                "open": self.closes,
                "high": [c * 1.002 for c in self.closes],
                "low": [c * 0.998 for c in self.closes],
                "close": self.closes,
                "volume": [100] * n,
            }
        )


class DummyClassifier:
    def __init__(self, up_probability):
        self.up_probability = up_probability

    def predict_proba(self, X):
        return np.array([[1 - self.up_probability, self.up_probability]])


class DummyExchange:
    def __init__(self):
        self.buy_orders = []
        self.sell_orders = []

    def create_market_buy_order(self, symbol, quantity):
        self.buy_orders.append((symbol, quantity))

    def create_market_sell_order(self, symbol, quantity):
        self.sell_orders.append((symbol, quantity))

    def fetch_balance(self):
        return {}  # a fresh dummy exchange starts flat - nothing for reconcile_with_exchange to find


class AlwaysFailingExchange:
    def create_market_buy_order(self, symbol, quantity):
        raise ConnectionError("simulated persistent exchange outage")

    def create_market_sell_order(self, symbol, quantity):
        raise ConnectionError("simulated persistent exchange outage")

    def fetch_balance(self):
        return {}


def _build_orchestrator(tmp_path, model, closes, initial_capital=10000.0):
    portfolio_manager = PortfolioManager(initial_capital=initial_capital)
    journal = Journal(db_path=str(tmp_path / "journal.db"))
    exchange = DummyExchange()

    prediction_agent = PredictionAgent(model_dir=tmp_path, auto_load=False)
    prediction_agent.set_model("BTC/USDT", model, feature_columns=["close"])

    orchestrator = Orchestrator(
        market_monitor=MarketMonitorAgent(symbols=["BTC/USDT"], feeds={"BTC/USDT": FakeFeed(closes)}),
        prediction=prediction_agent,
        risk=RiskAgent(max_position_size=0.5),
        execution=ExecutionAgent(portfolio=portfolio_manager, exchange=exchange),
        portfolio=PortfolioAgent(portfolio=portfolio_manager, journal=journal),
        journal=journal,
    )
    return orchestrator, portfolio_manager, journal, exchange


def test_buy_cycle_opens_position_and_journals_decision(tmp_path):
    closes = [float(i) for i in range(1, 21)]
    orchestrator, portfolio_manager, journal, exchange = _build_orchestrator(
        tmp_path, model=DummyClassifier(up_probability=0.9), closes=closes
    )

    orchestrator.run_cycle()

    assert "BTC/USDT" in portfolio_manager.positions
    assert exchange.buy_orders

    decisions = journal.recent_decisions()
    assert len(decisions) == 1
    assert decisions[0]["signal"] == "BUY"
    assert decisions[0]["risk_approved"] is True
    assert decisions[0]["action"] == "opened_position"

    # mark_to_market ran at the end of the cycle
    assert len(portfolio_manager.equity_curve) == 2


def _fake_build_features_with_last_bar_condition(atr_pct, relative_volume):
    def fake_build_features(frame, *args, **kwargs):
        result = frame.copy()
        n = len(result)
        result["sma_fast_vs_slow"] = [0.05] * n
        result["adx_strength"] = [0.6] * n
        result["rsi_normalized"] = [0.5] * n
        result["atr_pct"] = [0.01] * (n - 1) + [atr_pct]
        result["relative_volume"] = [1.0] * (n - 1) + [relative_volume]
        return result

    return fake_build_features


def test_sits_out_entirely_during_panic_market_condition(tmp_path, monkeypatch):
    """Even a strongly bullish model must not trade through a panic spike -
    the sit-out is unconditional, not just another input to the signal."""
    closes = [float(i) for i in range(1, 21)]
    orchestrator, portfolio_manager, journal, exchange = _build_orchestrator(
        tmp_path, model=DummyClassifier(up_probability=0.9), closes=closes
    )
    monkeypatch.setattr(
        "trading_assistant.agents.market_monitor.build_features",
        _fake_build_features_with_last_bar_condition(atr_pct=0.20, relative_volume=1.0),
    )

    orchestrator.run_cycle()

    assert "BTC/USDT" not in portfolio_manager.positions
    assert exchange.buy_orders == []
    decisions = journal.recent_decisions()
    assert decisions[0]["action"] == "sit_out"
    assert decisions[0]["signal"] == "HOLD"
    assert decisions[0]["risk_approved"] is False
    assert decisions[0]["risk_reason"] == "market_condition:panic"
    assert decisions[0]["details"]["market_condition"] == "panic"


def test_sits_out_entirely_during_illiquid_market_condition(tmp_path, monkeypatch):
    closes = [float(i) for i in range(1, 21)]
    orchestrator, portfolio_manager, journal, exchange = _build_orchestrator(
        tmp_path, model=DummyClassifier(up_probability=0.9), closes=closes
    )
    monkeypatch.setattr(
        "trading_assistant.agents.market_monitor.build_features",
        _fake_build_features_with_last_bar_condition(atr_pct=0.01, relative_volume=0.05),
    )

    orchestrator.run_cycle()

    assert "BTC/USDT" not in portfolio_manager.positions
    assert exchange.buy_orders == []
    decisions = journal.recent_decisions()
    assert decisions[0]["action"] == "sit_out"
    assert decisions[0]["risk_reason"] == "market_condition:illiquid"


def test_normal_conditions_do_not_trigger_sit_out(tmp_path, monkeypatch):
    """Regression guard: confirms the sit-out wiring itself isn't
    accidentally firing on every cycle regardless of condition."""
    closes = [float(i) for i in range(1, 21)]
    orchestrator, portfolio_manager, journal, exchange = _build_orchestrator(
        tmp_path, model=DummyClassifier(up_probability=0.9), closes=closes
    )
    monkeypatch.setattr(
        "trading_assistant.agents.market_monitor.build_features",
        _fake_build_features_with_last_bar_condition(atr_pct=0.01, relative_volume=1.0),
    )

    orchestrator.run_cycle()

    assert "BTC/USDT" in portfolio_manager.positions
    decisions = journal.recent_decisions()
    assert decisions[0]["action"] == "opened_position"


def test_buy_cycle_persists_execution_quality_telemetry(tmp_path):
    closes = [float(i) for i in range(1, 21)]
    orchestrator, portfolio_manager, journal, exchange = _build_orchestrator(
        tmp_path, model=DummyClassifier(up_probability=0.9), closes=closes
    )

    orchestrator.run_cycle()

    records = journal.recent_execution_quality()
    assert len(records) == 1
    assert records[0]["symbol"] == "BTC/USDT"
    assert records[0]["side"] == "buy"
    assert records[0]["order_type"] == "market"


def test_sell_cycle_closes_existing_position_and_places_exchange_order(tmp_path):
    # Realistic price level: a monotonic decline ending near 1 (as a small
    # integer range would) makes late-bar close-to-close moves implausibly
    # large in percentage terms (e.g. 2 -> 1 is -50%), which would falsely
    # trip the panic market-condition guard - see the FakeFeed docstring
    # above for the analogous high/low fix.
    closes = [float(i) for i in range(120, 100, -1)]
    orchestrator, portfolio_manager, journal, exchange = _build_orchestrator(
        tmp_path, model=DummyClassifier(up_probability=0.1), closes=closes
    )
    portfolio_manager.open_position("BTC/USDT", price=closes[0], quantity=1.0)

    orchestrator.run_cycle()

    assert "BTC/USDT" not in portfolio_manager.positions
    assert exchange.sell_orders

    decisions = journal.recent_decisions()
    assert decisions[0]["signal"] == "SELL"
    assert decisions[0]["action"] == "closed_position"


def test_full_round_trip_writes_a_complete_trade_audit_record(tmp_path):
    """The immutable audit trail's core promise: a position opened THROUGH
    the orchestrator (so its entry context is durably journaled) and later
    closed produces exactly one trade_audit record carrying both the real
    entry context (price, confidence path, regime) and the real exit
    context together - not just the raw buy/sell fills the trades table
    already had."""
    closes = [float(i) for i in range(101, 121)]
    orchestrator, portfolio_manager, journal, exchange = _build_orchestrator(
        tmp_path, model=DummyClassifier(up_probability=0.9), closes=closes
    )

    orchestrator.run_cycle()  # BUY: opens the position through the real journaled path
    assert "BTC/USDT" in portfolio_manager.positions

    # Swap to a sell-biased model so the next cycle closes what was just opened.
    orchestrator.prediction.set_model("BTC/USDT", DummyClassifier(up_probability=0.1), feature_columns=["close"])
    orchestrator.run_cycle()  # SELL: closes it

    assert "BTC/USDT" not in portfolio_manager.positions
    records = journal.recent_trade_audit(symbol="BTC/USDT")
    assert len(records) == 1
    record = records[0]
    assert record["side"] == "long"
    assert record["entry_price"] is not None
    assert record["exit_price"] is not None
    assert record["quantity"] > 0
    assert record["strategy_name"] == "ai_ensemble"  # no tournament champion in this path
    assert record["market_regime"] is not None
    assert "SELL signal" in record["exit_reason"]
    assert record["realized_pnl"] is not None
    assert record["corrects_id"] is None


class StopTriggeredExchange(DummyExchange):
    """Reports a resting protective stop order as already filled - proves
    the orchestrator detects this and reconciles local state BEFORE
    evaluating any new signal, rather than silently believing the position
    is still open."""

    def __init__(self, fill_price):
        super().__init__()
        self.fill_price = fill_price

    def fetch_order(self, order_id, symbol):
        return {"id": order_id, "symbol": symbol, "status": "closed", "average": self.fill_price}


def test_triggered_protective_stop_is_reconciled_before_evaluating_a_new_signal(tmp_path):
    """The core correctness case the whole protective-stop feature exists
    for: once the exchange fills a resting stop, this process's in-memory
    portfolio has no way to know unless something checks - without this,
    the position would stay "open" in memory after the exchange already
    closed it, corrupting capital/equity and causing a future SELL to fail
    with insufficient balance."""
    closes = [100.0] * 20  # flat/HOLD-biased so the model itself would take no action this cycle
    portfolio_manager = PortfolioManager(initial_capital=10000.0)
    journal = Journal(db_path=str(tmp_path / "journal.db"))
    exchange = StopTriggeredExchange(fill_price=95.0)

    prediction_agent = PredictionAgent(model_dir=tmp_path, auto_load=False)
    prediction_agent.set_model("BTC/USDT", DummyClassifier(up_probability=0.5), feature_columns=["close"])

    orchestrator = Orchestrator(
        market_monitor=MarketMonitorAgent(symbols=["BTC/USDT"], feeds={"BTC/USDT": FakeFeed(closes)}),
        prediction=prediction_agent,
        risk=RiskAgent(max_position_size=0.5),
        execution=ExecutionAgent(portfolio=portfolio_manager, exchange=exchange, stop_loss_pct=0.02),
        portfolio=PortfolioAgent(portfolio=portfolio_manager, journal=journal),
        journal=journal,
    )
    portfolio_manager.open_position("BTC/USDT", price=100.0, quantity=1.0)
    portfolio_manager.positions["BTC/USDT"]["stop_order_id"] = "42"

    orchestrator.run_cycle()

    assert "BTC/USDT" not in portfolio_manager.positions  # reconciled closed, not left dangling
    trades = journal.recent_trades()
    assert trades[0]["side"] == "sell"
    assert trades[0]["price"] == 95.0  # the stop's ACTUAL fill price, not the current market price

    decisions = journal.recent_decisions()
    assert decisions[0]["signal"] == "STOP_LOSS_TRIGGERED"
    assert decisions[0]["action"] == "closed_position"
    # the model's own signal for this cycle never ran - the symbol was
    # already reconciled and skipped for the rest of this cycle
    assert len(decisions) == 1

    audit_records = journal.recent_trade_audit(symbol="BTC/USDT")
    assert len(audit_records) == 1
    assert audit_records[0]["side"] == "long"
    assert audit_records[0]["exit_price"] == 95.0
    assert audit_records[0]["quantity"] == 1.0
    assert "protective stop-loss triggered" in audit_records[0]["exit_reason"]


def test_triggered_protective_stop_raises_an_alert_when_alert_manager_configured(tmp_path):
    from trading_assistant.agents.alerting import AlertManager

    closes = [100.0] * 20
    portfolio_manager = PortfolioManager(initial_capital=10000.0)
    journal = Journal(db_path=str(tmp_path / "journal.db"))
    exchange = StopTriggeredExchange(fill_price=95.0)
    alert_manager = AlertManager(journal=journal)

    prediction_agent = PredictionAgent(model_dir=tmp_path, auto_load=False)
    prediction_agent.set_model("BTC/USDT", DummyClassifier(up_probability=0.5), feature_columns=["close"])

    orchestrator = Orchestrator(
        market_monitor=MarketMonitorAgent(symbols=["BTC/USDT"], feeds={"BTC/USDT": FakeFeed(closes)}),
        prediction=prediction_agent,
        risk=RiskAgent(max_position_size=0.5),
        execution=ExecutionAgent(portfolio=portfolio_manager, exchange=exchange, stop_loss_pct=0.02),
        portfolio=PortfolioAgent(portfolio=portfolio_manager, journal=journal),
        journal=journal,
        alert_manager=alert_manager,
    )
    portfolio_manager.open_position("BTC/USDT", price=100.0, quantity=1.0)
    portfolio_manager.positions["BTC/USDT"]["stop_order_id"] = "42"

    orchestrator.run_cycle()

    alerts = journal.recent_alerts()
    assert len(alerts) == 1
    assert alerts[0]["category"] == "stop_loss_triggered"
    assert alerts[0]["severity"] == "warning"
    assert "BTC/USDT" in alerts[0]["message"]


def test_hold_cycle_takes_no_action(tmp_path):
    closes = [100.0] * 20  # flat prices -> no strong signal either way with a HOLD-biased model
    orchestrator, portfolio_manager, journal, exchange = _build_orchestrator(
        tmp_path, model=DummyClassifier(up_probability=0.5), closes=closes
    )

    orchestrator.run_cycle()

    assert portfolio_manager.positions == {}
    decisions = journal.recent_decisions()
    assert decisions[0]["signal"] == "HOLD"
    assert decisions[0]["action"] == "no_action"


def test_one_symbol_failing_does_not_abort_the_rest_of_the_cycle(tmp_path):
    """Regression: _process_symbol raising for one symbol (e.g. a stale or
    incompatible model file) used to propagate straight out of the
    per-symbol loop in run_cycle, skipping every symbol still left in that
    cycle rather than just the broken one - and since retrain happens on a
    24h cadence, this could otherwise repeat every single cycle in between."""

    class RaisingPredictionAgent:
        def predict(self, symbol, features):
            if symbol == "BTC/USDT":
                raise ValueError("simulated stale/incompatible model")
            return PredictionResult(symbol=symbol, signal=Signal.HOLD, confidence=None)

    closes = [100.0] * 20
    portfolio_manager = PortfolioManager(initial_capital=10000.0)
    journal = Journal(db_path=str(tmp_path / "journal.db"))
    exchange = DummyExchange()

    orchestrator = Orchestrator(
        market_monitor=MarketMonitorAgent(
            symbols=["BTC/USDT", "ETH/USDT"],
            feeds={"BTC/USDT": FakeFeed(closes), "ETH/USDT": FakeFeed(closes)},
        ),
        prediction=RaisingPredictionAgent(),
        risk=RiskAgent(max_position_size=0.5),
        execution=ExecutionAgent(portfolio=portfolio_manager, exchange=exchange),
        portfolio=PortfolioAgent(portfolio=portfolio_manager, journal=journal),
        journal=journal,
    )

    orchestrator.run_cycle()  # must not raise

    decisions = journal.recent_decisions()
    symbols_journaled = {d["symbol"] for d in decisions}
    assert symbols_journaled == {"ETH/USDT"}  # BTC/USDT failed before journaling; ETH/USDT still got through


def test_run_forever_respects_max_cycles(tmp_path):
    closes = [100.0] * 20
    orchestrator, _, journal, _ = _build_orchestrator(
        tmp_path, model=DummyClassifier(up_probability=0.5), closes=closes
    )
    orchestrator.poll_interval_seconds = 0

    asyncio.run(orchestrator.run_forever(max_cycles=3))

    assert len(journal.recent_decisions(limit=10)) == 3


def test_request_shutdown_before_start_exits_immediately_with_zero_cycles(tmp_path):
    orchestrator, _, journal, _ = _build_orchestrator(
        tmp_path, model=DummyClassifier(up_probability=0.5), closes=[100.0] * 20
    )
    orchestrator.poll_interval_seconds = 9999  # would hang the test if shutdown didn't short-circuit it

    orchestrator.request_shutdown()

    async def run_with_timeout():
        await asyncio.wait_for(orchestrator.run_forever(), timeout=5.0)

    asyncio.run(run_with_timeout())  # must not hit the 5s timeout

    assert journal.recent_decisions(limit=10) == []


def test_request_shutdown_stops_the_loop_promptly_instead_of_waiting_the_full_poll_interval(tmp_path):
    """The core fix: request_shutdown() firing mid-sleep must wake the loop
    up immediately (an interruptible wait), not leave it blocked for up to
    poll_interval_seconds - matters because systemd/Docker only grant a
    bounded grace period before escalating to SIGKILL."""
    orchestrator, _, journal, _ = _build_orchestrator(
        tmp_path, model=DummyClassifier(up_probability=0.5), closes=[100.0] * 20
    )
    orchestrator.poll_interval_seconds = 9999  # would hang well past any reasonable test timeout

    async def run_and_request_shutdown_after_one_cycle():
        task = asyncio.create_task(orchestrator.run_forever())
        await asyncio.sleep(0.2)  # let the first cycle complete and the loop enter its interruptible sleep
        orchestrator.request_shutdown()
        await asyncio.wait_for(task, timeout=5.0)  # must exit promptly, not after 9999s

    asyncio.run(run_and_request_shutdown_after_one_cycle())

    assert len(journal.recent_decisions(limit=10)) == 1  # exactly the one cycle that ran before shutdown


def test_run_forever_writes_a_heartbeat_start_and_one_cycle_heartbeat_per_cycle(tmp_path):
    closes = [100.0] * 20
    orchestrator, _, journal, _ = _build_orchestrator(
        tmp_path, model=DummyClassifier(up_probability=0.5), closes=closes
    )
    orchestrator.poll_interval_seconds = 0

    asyncio.run(orchestrator.run_forever(max_cycles=3))

    assert journal.latest_heartbeat_start() is not None
    cycles = journal.recent_heartbeat_cycles(limit=10)
    assert len(cycles) == 3
    assert all(c["status"] == "ok" for c in cycles)
    assert [c["cycle_number"] for c in cycles] == [2, 1, 0]  # most recent first


def test_run_forever_records_an_error_heartbeat_when_a_cycle_raises(tmp_path):
    closes = [100.0] * 20
    orchestrator, _, journal, _ = _build_orchestrator(
        tmp_path, model=DummyClassifier(up_probability=0.5), closes=closes
    )
    orchestrator.poll_interval_seconds = 0

    def _boom():
        raise RuntimeError("simulated cycle failure")

    orchestrator.run_cycle = _boom

    asyncio.run(orchestrator.run_forever(max_cycles=1))

    cycles = journal.recent_heartbeat_cycles(limit=10)
    assert len(cycles) == 1
    assert cycles[0]["status"] == "error"


class FakeEnsembleModel:
    """Mimics ml.predictor.EnsemblePredictor (predict / predict_with_confidence, no predict_proba)."""

    def __init__(self, value):
        self.value = value

    def train(self, X, y):
        pass

    def predict(self, X):
        return np.full(len(X), self.value)

    def predict_with_confidence(self, X):
        return np.full(len(X), self.value), np.full(len(X), 0.7)


def test_warm_start_retrains_before_first_cycle_and_can_trade_on_promoted_model(tmp_path):
    closes = [100.0 + i * 0.5 for i in range(300)]  # clean uptrend, enough history for indicators + backtest
    feed = FakeFeed(closes)

    portfolio_manager = PortfolioManager(initial_capital=10000.0)
    journal = Journal(db_path=str(tmp_path / "journal.db"))
    exchange = DummyExchange()
    prediction_agent = PredictionAgent(model_dir=tmp_path, auto_load=False)
    learning_agent = LearningAgent(
        prediction_agent=prediction_agent,
        min_sharpe_to_promote=0.01,
        max_drawdown_to_promote=0.9,
        min_pct_folds_profitable=0.0,
        train_window_bars=100,
        test_window_bars=20,
        target_folds=3,
        min_folds_required=2,
        model_factory=lambda: FakeEnsembleModel(0.9),
        feeds={"BTC/USDT": feed},
        journal=journal,
    )
    orchestrator = Orchestrator(
        market_monitor=MarketMonitorAgent(symbols=["BTC/USDT"], feeds={"BTC/USDT": feed}),
        prediction=prediction_agent,
        risk=RiskAgent(max_position_size=0.5),
        execution=ExecutionAgent(portfolio=portfolio_manager, exchange=exchange),
        portfolio=PortfolioAgent(portfolio=portfolio_manager, journal=journal),
        journal=journal,
        learning=learning_agent,
        retrain_interval_seconds=None,  # only exercise the warm-start path here
    )

    asyncio.run(orchestrator.run_forever(max_cycles=1))

    assert "BTC/USDT" in learning_agent.last_promoted_metrics
    decisions = journal.recent_decisions()
    assert decisions[0]["signal"] == "BUY"
    assert decisions[0]["action"] == "opened_position"


def test_repeated_exchange_failures_trip_circuit_breaker_and_halt_further_buys(tmp_path):
    closes = [float(i) for i in range(1, 21)]
    portfolio_manager = PortfolioManager(initial_capital=10000.0)
    journal = Journal(db_path=str(tmp_path / "journal.db"))
    exchange = AlwaysFailingExchange()

    prediction_agent = PredictionAgent(model_dir=tmp_path, auto_load=False)
    prediction_agent.set_model("BTC/USDT", DummyClassifier(up_probability=0.9), feature_columns=["close"])

    risk_agent = RiskAgent(max_position_size=0.5, max_consecutive_errors=3)
    orchestrator = Orchestrator(
        market_monitor=MarketMonitorAgent(symbols=["BTC/USDT"], feeds={"BTC/USDT": FakeFeed(closes)}),
        prediction=prediction_agent,
        risk=risk_agent,
        execution=ExecutionAgent(portfolio=portfolio_manager, exchange=exchange, max_retries=1, backoff_seconds=0),
        portfolio=PortfolioAgent(portfolio=portfolio_manager, journal=journal),
        journal=journal,
    )

    # 3 cycles, each a failed buy attempt (position never opens since the exchange always errors)
    for _ in range(3):
        orchestrator.run_cycle()

    assert risk_agent.circuit_breaker.tripped is True

    # a 4th cycle must be rejected by the risk gate before ever touching the exchange again
    orchestrator.run_cycle()

    decisions = journal.recent_decisions(limit=1)
    assert decisions[0]["risk_approved"] is False
    assert "circuit breaker" in decisions[0]["risk_reason"]
    assert "BTC/USDT" not in portfolio_manager.positions


class AlwaysFailingFeed:
    def fetch_ohlcv(self, limit=100):
        raise ConnectionError("simulated market data outage")


def test_persistent_market_data_failure_does_not_trip_execution_circuit_breaker(tmp_path):
    """Market-data/network failures are availability incidents, not execution
    failures. They must not consume the execution circuit-breaker budget."""
    portfolio_manager = PortfolioManager(initial_capital=10000.0)
    journal = Journal(db_path=str(tmp_path / "journal.db"))
    prediction_agent = PredictionAgent(model_dir=tmp_path, auto_load=False)
    risk_agent = RiskAgent(max_position_size=0.5, max_consecutive_errors=3)

    orchestrator = Orchestrator(
        market_monitor=MarketMonitorAgent(symbols=["BTC/USDT"], feeds={"BTC/USDT": AlwaysFailingFeed()}),
        prediction=prediction_agent,
        risk=risk_agent,
        execution=ExecutionAgent(portfolio=portfolio_manager, exchange=DummyExchange()),
        portfolio=PortfolioAgent(portfolio=portfolio_manager, journal=journal),
        journal=journal,
    )

    for _ in range(3):
        orchestrator.run_cycle()

    assert risk_agent.circuit_breaker.tripped is False


class AlternatingClassifier:
    """Emits BUY on odd calls, SELL on even calls."""

    def __init__(self):
        self.call_count = 0

    def predict_proba(self, X):
        self.call_count += 1
        up_probability = 0.9 if self.call_count % 2 == 1 else 0.1
        return np.array([[1 - up_probability, up_probability]])


def test_no_action_cycles_do_not_reset_circuit_breaker_counter(tmp_path):
    """Regression test: no_action outcomes (here, a SELL signal for a symbol
    that was never successfully bought, since every buy attempt fails at
    the exchange) were previously reported as a circuit-breaker "success",
    resetting the consecutive-error counter and masking the real,
    interleaved execution failures."""
    closes = [float(i) for i in range(1, 21)]
    portfolio_manager = PortfolioManager(initial_capital=10000.0)
    journal = Journal(db_path=str(tmp_path / "journal.db"))
    exchange = AlwaysFailingExchange()  # every BUY attempt fails; a position never opens

    prediction_agent = PredictionAgent(model_dir=tmp_path, auto_load=False)
    prediction_agent.set_model("BTC/USDT", AlternatingClassifier(), feature_columns=["close"])

    risk_agent = RiskAgent(max_position_size=0.5, max_consecutive_errors=3)
    orchestrator = Orchestrator(
        market_monitor=MarketMonitorAgent(symbols=["BTC/USDT"], feeds={"BTC/USDT": FakeFeed(closes)}),
        prediction=prediction_agent,
        risk=risk_agent,
        execution=ExecutionAgent(portfolio=portfolio_manager, exchange=exchange, max_retries=1, backoff_seconds=0),
        portfolio=PortfolioAgent(portfolio=portfolio_manager, journal=journal),
        journal=journal,
    )

    # cycle 1: BUY -> exchange fails -> error (count=1)
    # cycle 2: SELL -> not holding -> no_action (must not reset or count)
    # cycle 3: BUY -> exchange fails -> error (count=2)
    # cycle 4: SELL -> not holding -> no_action (must not reset or count)
    # cycle 5: BUY -> exchange fails -> error (count=3) -> trips
    # Under the old bug, the interleaved "successes" would reset the count
    # every other cycle and the breaker would never trip.
    for _ in range(5):
        orchestrator.run_cycle()

    assert risk_agent.circuit_breaker.tripped is True


def test_daily_loss_uses_pre_trade_capital_not_post_trade(tmp_path):
    """Regression test: record_realized_pnl was previously called with
    capital AFTER close_position had already added back the sale proceeds,
    understating the loss fraction right when it matters most."""
    closes = [100.0] * 19 + [75.0]  # current price 75.0, a loss vs. the 100.0 entry
    orchestrator, portfolio_manager, journal, exchange = _build_orchestrator(
        tmp_path, model=DummyClassifier(up_probability=0.1), closes=closes
    )
    portfolio_manager.open_position("BTC/USDT", price=100.0, quantity=20.0)  # capital: 10000 -> 8000

    orchestrator.run_cycle()

    # proceeds = 75*20 = 1500, pnl = 1500 - 2000 = -500.
    # Pre-trade capital was 8000, so daily_pnl should be -500/8000 = -0.0625.
    # The bug would have used post-trade capital (9500) -> ~-0.0526 instead.
    assert orchestrator.risk.risk_manager.daily_pnl == pytest.approx(-0.0625)


class FakeDiscoveryExchange:
    def __init__(self, markets, tickers):
        self._markets = markets
        self._tickers = tickers

    def load_markets(self):
        return self._markets

    def fetch_tickers(self):
        return self._tickers


def test_warm_start_reconciles_portfolio_with_real_exchange_balance_after_discovery(tmp_path):
    """Regression: PortfolioManager's positions live only in process memory
    with no persistence across restarts - a real testnet account was found
    holding a real position the freshly-restarted in-memory portfolio had
    no record of at all, because that symbol was only in the DISCOVERED
    universe, not the static seed list. Reconciliation must run AFTER
    symbol discovery (so it covers the full discovered universe) and
    BEFORE the first live cycle can act."""
    from trading_assistant.agents.symbol_discovery import SymbolDiscoveryAgent

    closes = [100.0] * 20
    portfolio_manager = PortfolioManager(initial_capital=10000.0)
    journal = Journal(db_path=str(tmp_path / "journal.db"))
    prediction_agent = PredictionAgent(model_dir=tmp_path, auto_load=False)

    class ReconcileAwareExchange(DummyExchange):
        """Discovers a symbol not in the static seed AND reports a real
        balance for it - proves reconciliation sees the post-discovery list."""

        def fetch_balance(self):
            return {"SOL": {"total": 42.0}}

        def fetch_ticker(self, symbol):
            return {"last": 150.0}  # no journal history for SOL/USDT - fully unexplained, needs a current price

    exchange = ReconcileAwareExchange()
    markets = {"SOL/USDT": {"quote": "USDT", "active": True, "type": "spot"}}
    tickers = {"SOL/USDT": {"quoteVolume": 50_000_000, "bid": 150.0, "ask": 150.1}}
    discovery_agent = SymbolDiscoveryAgent(FakeDiscoveryExchange(markets, tickers), min_24h_volume_usd=0)

    market_monitor = MarketMonitorAgent(
        symbols=["BTC/USDT"], feeds={"BTC/USDT": FakeFeed(closes), "SOL/USDT": FakeFeed(closes)}
    )

    orchestrator = Orchestrator(
        market_monitor=market_monitor,
        prediction=prediction_agent,
        risk=RiskAgent(max_position_size=0.5),
        execution=ExecutionAgent(portfolio=portfolio_manager, exchange=exchange),
        portfolio=PortfolioAgent(portfolio=portfolio_manager, journal=journal),
        journal=journal,
        symbol_discovery=discovery_agent,
        symbol_discovery_interval_seconds=None,
    )

    asyncio.run(orchestrator.run_forever(max_cycles=1))

    assert "SOL/USDT" in portfolio_manager.positions
    assert portfolio_manager.positions["SOL/USDT"]["quantity"] == 42.0


def test_warm_start_syncs_capital_from_real_exchange_balance_when_live(tmp_path):
    """Regression test for a real gap found going live: capital was ALWAYS
    just config's initial_capital, with nothing ever syncing it to the
    real account's actual balance - a fresh live account with $0 would
    still report the full configured capital and try to size real orders
    against it."""
    closes = [100.0] * 20
    portfolio_manager = PortfolioManager(initial_capital=1000.0)
    journal = Journal(db_path=str(tmp_path / "journal.db"))
    prediction_agent = PredictionAgent(model_dir=tmp_path, auto_load=False)

    class LiveBalanceExchange(DummyExchange):
        def fetch_balance(self):
            return {"USDT": {"free": 17.25, "total": 17.25}}

    exchange = LiveBalanceExchange()
    market_monitor = MarketMonitorAgent(symbols=["BTC/USDT"], feeds={"BTC/USDT": FakeFeed(closes)})

    orchestrator = Orchestrator(
        market_monitor=market_monitor,
        prediction=prediction_agent,
        risk=RiskAgent(max_position_size=0.5),
        execution=ExecutionAgent(portfolio=portfolio_manager, exchange=exchange),
        portfolio=PortfolioAgent(portfolio=portfolio_manager, journal=journal),
        journal=journal,
        is_live=True,
        quote_currency="USDT",
    )

    asyncio.run(orchestrator.run_forever(max_cycles=1))

    assert portfolio_manager.capital == 17.25  # real free balance, not the configured 1000.0


def test_warm_start_does_not_sync_capital_when_not_live(tmp_path):
    """Testnet/paper must keep the old behavior unchanged: capital stays a
    controlled, configured constant regardless of testnet's messy real
    balance (see PortfolioAgent.sync_capital_with_exchange's docstring)."""
    closes = [100.0] * 20
    portfolio_manager = PortfolioManager(initial_capital=1000.0)
    journal = Journal(db_path=str(tmp_path / "journal.db"))
    prediction_agent = PredictionAgent(model_dir=tmp_path, auto_load=False)

    class LiveBalanceExchange(DummyExchange):
        def fetch_balance(self):
            return {"USDT": {"free": 17.25, "total": 17.25}}

    exchange = LiveBalanceExchange()
    market_monitor = MarketMonitorAgent(symbols=["BTC/USDT"], feeds={"BTC/USDT": FakeFeed(closes)})

    orchestrator = Orchestrator(
        market_monitor=market_monitor,
        prediction=prediction_agent,
        risk=RiskAgent(max_position_size=0.5),
        execution=ExecutionAgent(portfolio=portfolio_manager, exchange=exchange),
        portfolio=PortfolioAgent(portfolio=portfolio_manager, journal=journal),
        journal=journal,
        # is_live defaults to False
    )

    asyncio.run(orchestrator.run_forever(max_cycles=1))

    assert portfolio_manager.capital == 1000.0  # unchanged - testnet/paper keeps the configured constant


def test_warm_start_alerts_when_reconciliation_backfill_leaves_a_position_unprotected(tmp_path):
    """A real position surviving a restart with no resting stop, where the
    backfill attempt ALSO fails, is direct capital-at-risk right from warm
    start - must raise a real alert, not just the existing error log."""
    from trading_assistant.agents.alerting import AlertManager

    closes = [100.0] * 20
    portfolio_manager = PortfolioManager(initial_capital=10000.0)
    journal = Journal(db_path=str(tmp_path / "journal.db"))
    prediction_agent = PredictionAgent(model_dir=tmp_path, auto_load=False)
    alert_manager = AlertManager(journal=journal)

    class UnprotectableExchange(DummyExchange):
        def fetch_balance(self):
            return {"BTC": {"total": 1.0}}

        def fetch_ticker(self, symbol):
            return {"last": 100.0}

        def fetch_open_orders(self, symbol):
            return []

        def create_order(self, symbol, type_, side, amount, price=None, params=None):
            raise ConnectionError("simulated stop placement failure")

    exchange = UnprotectableExchange()
    market_monitor = MarketMonitorAgent(symbols=["BTC/USDT"], feeds={"BTC/USDT": FakeFeed(closes)})

    orchestrator = Orchestrator(
        market_monitor=market_monitor,
        prediction=prediction_agent,
        risk=RiskAgent(max_position_size=0.5),
        execution=ExecutionAgent(portfolio=portfolio_manager, exchange=exchange, stop_loss_pct=0.02),
        portfolio=PortfolioAgent(portfolio=portfolio_manager, journal=journal),
        journal=journal,
        alert_manager=alert_manager,
    )

    asyncio.run(orchestrator.run_forever(max_cycles=1))

    alerts = journal.recent_alerts()
    unprotected_alerts = [a for a in alerts if a["category"] == "position_unprotected"]
    assert len(unprotected_alerts) == 1
    assert unprotected_alerts[0]["severity"] == "critical"
    assert "BTC/USDT" in unprotected_alerts[0]["message"]


class BackfillableExchange(DummyExchange):
    """Starts unable to place a stop (simulating the real fee-erosion/
    lot-step incident), then succeeds once `can_place_stop` flips true -
    simulating price moving back in favor enough to clear the exchange's
    real minimum notional again."""

    def __init__(self, free_balance):
        super().__init__()
        self._free_balance = free_balance
        self.can_place_stop = False
        self.stop_orders = []

    def fetch_balance(self):
        return {"BTC": {"free": self._free_balance}}

    def create_order(self, symbol, type_, side, amount, price=None, params=None):
        if not self.can_place_stop:
            raise Exception("binance InsufficientFunds")
        self.stop_orders.append((symbol, type_, side, amount, price, params))
        return {"id": "backfilled-1"}


class TestRetryMissingProtectiveStop:
    """Regression for a real incident: a live BTC/USDT buy filled, but its
    initial protective-stop placement failed (exchange fee erosion + lot-
    size rounding left slightly less than the requested quantity) - and
    there was NO automatic retry anywhere in the per-cycle loop, only at
    the next full process restart (reconcile_with_exchange runs once, at
    warm start). Under real 24/7 operation a position could stay
    genuinely unprotected indefinitely. _retry_missing_protective_stop
    must self-heal every cycle using the exchange's actual current free
    balance."""

    def _orchestrator_with_unprotected_position(self, tmp_path, exchange):
        portfolio_manager = PortfolioManager(initial_capital=10000.0)
        portfolio_manager.positions["BTC/USDT"] = {
            "price": 100.0,
            "quantity": 1.0,
            "cost": 100.0,
            "stop_loss": None,
            "take_profit": None,
            "timestamp": pd.Timestamp.now(),
            "stop_order_id": None,
            "intended_stop_loss_pct": 0.02,
        }
        journal = Journal(db_path=str(tmp_path / "journal.db"))
        prediction_agent = PredictionAgent(model_dir=tmp_path, auto_load=False)
        orchestrator = Orchestrator(
            market_monitor=MarketMonitorAgent(symbols=["BTC/USDT"], feeds={"BTC/USDT": FakeFeed([100.0] * 20)}),
            prediction=prediction_agent,
            risk=RiskAgent(max_position_size=0.5),
            execution=ExecutionAgent(portfolio=portfolio_manager, exchange=exchange, stop_loss_pct=0.02),
            portfolio=PortfolioAgent(portfolio=portfolio_manager, journal=journal),
            journal=journal,
        )
        return orchestrator, portfolio_manager

    def test_retries_and_succeeds_using_the_exchanges_actual_free_balance(self, tmp_path):
        exchange = BackfillableExchange(free_balance=0.999)  # slightly less than the original 1.0 (fee-eroded)
        exchange.can_place_stop = True
        orchestrator, portfolio_manager = self._orchestrator_with_unprotected_position(tmp_path, exchange)

        orchestrator._retry_missing_protective_stop("BTC/USDT")

        assert portfolio_manager.positions["BTC/USDT"]["stop_order_id"] == "backfilled-1"
        assert exchange.stop_orders[0][3] == pytest.approx(0.999)  # used the REAL free balance, not the stale 1.0

    def test_still_a_no_op_when_the_exchange_still_refuses(self, tmp_path):
        exchange = BackfillableExchange(free_balance=0.999)
        exchange.can_place_stop = False  # still can't clear the minimum, e.g. price hasn't recovered
        orchestrator, portfolio_manager = self._orchestrator_with_unprotected_position(tmp_path, exchange)

        orchestrator._retry_missing_protective_stop("BTC/USDT")

        assert portfolio_manager.positions["BTC/USDT"]["stop_order_id"] is None

    def test_no_op_when_the_position_already_has_a_stop(self, tmp_path):
        exchange = BackfillableExchange(free_balance=0.999)
        exchange.can_place_stop = True
        orchestrator, portfolio_manager = self._orchestrator_with_unprotected_position(tmp_path, exchange)
        portfolio_manager.positions["BTC/USDT"]["stop_order_id"] = "already-protected"

        orchestrator._retry_missing_protective_stop("BTC/USDT")

        assert exchange.stop_orders == []
        assert portfolio_manager.positions["BTC/USDT"]["stop_order_id"] == "already-protected"

    def test_no_op_when_there_is_no_open_position(self, tmp_path):
        exchange = BackfillableExchange(free_balance=0.999)
        exchange.can_place_stop = True
        orchestrator, _ = self._orchestrator_with_unprotected_position(tmp_path, exchange)
        del orchestrator.portfolio.portfolio.positions["BTC/USDT"]

        orchestrator._retry_missing_protective_stop("BTC/USDT")  # must not raise

        assert exchange.stop_orders == []


def test_symbol_discovery_expands_watchlist_and_preserves_held_positions(tmp_path):
    """The system should not only ever know about a hardcoded symbol list -
    SymbolDiscoveryAgent should expand the live watchlist, and a symbol the
    system currently holds a position in must never be dropped even if
    discovery's top-N ranking would otherwise exclude it."""
    from trading_assistant.agents.symbol_discovery import SymbolDiscoveryAgent

    closes = [100.0] * 20
    portfolio_manager = PortfolioManager(initial_capital=10000.0)
    portfolio_manager.open_position("HELD/USDT", price=50.0, quantity=1.0)  # discovery won't rank this
    journal = Journal(db_path=str(tmp_path / "journal.db"))
    exchange = DummyExchange()
    prediction_agent = PredictionAgent(model_dir=tmp_path, auto_load=False)

    markets = {
        "BTC/USDT": {"quote": "USDT", "active": True, "type": "spot"},
        "SOL/USDT": {"quote": "USDT", "active": True, "type": "spot"},
    }
    tickers = {
        "BTC/USDT": {"quoteVolume": 50_000_000, "bid": 60000.0, "ask": 60001.0},
        "SOL/USDT": {"quoteVolume": 20_000_000, "bid": 150.0, "ask": 150.1},
    }
    discovery_agent = SymbolDiscoveryAgent(
        FakeDiscoveryExchange(markets, tickers), min_24h_volume_usd=0, max_symbols=2
    )

    market_monitor = MarketMonitorAgent(
        symbols=["BTC/USDT"],
        feeds={"BTC/USDT": FakeFeed(closes), "SOL/USDT": FakeFeed(closes), "HELD/USDT": FakeFeed(closes)},
    )

    orchestrator = Orchestrator(
        market_monitor=market_monitor,
        prediction=prediction_agent,
        risk=RiskAgent(max_position_size=0.5),
        execution=ExecutionAgent(portfolio=portfolio_manager, exchange=exchange),
        portfolio=PortfolioAgent(portfolio=portfolio_manager, journal=journal),
        journal=journal,
        symbol_discovery=discovery_agent,
        symbol_discovery_interval_seconds=None,  # only exercise the warm-start discovery path here
    )

    asyncio.run(orchestrator.run_forever(max_cycles=1))

    assert "BTC/USDT" in market_monitor.symbols
    assert "SOL/USDT" in market_monitor.symbols  # newly discovered by volume ranking
    assert "HELD/USDT" in market_monitor.symbols  # open position - must never be dropped


def test_warm_start_runs_tournament_immediately_instead_of_waiting_for_first_interval(tmp_path):
    """Regression: without a warm-start tournament run, winner_history's
    first data point wouldn't land until tournament_interval_seconds later
    (default 24h in production config) - and stability_score() needs at
    least 2 runs before it means anything, so every skipped warm-start is a
    full cycle's delay before the stability track record even starts."""
    from trading_assistant.agents.strategy_manager import StrategyCandidate, StrategyManager
    from trading_assistant.strategies.implementations import SMACrossoverStrategy

    closes = [100.0 + i * 0.5 for i in range(300)]
    feed = FakeFeed(closes)
    portfolio_manager = PortfolioManager(initial_capital=10000.0)
    journal = Journal(db_path=str(tmp_path / "journal.db"))
    exchange = DummyExchange()
    prediction_agent = PredictionAgent(model_dir=tmp_path, auto_load=False)

    strategy_manager = StrategyManager(
        candidates=[StrategyCandidate("sma_crossover", "rule", SMACrossoverStrategy)],
        train_window_bars=60,
        test_window_bars=20,
        min_folds_required=2,
        feeds={"BTC/USDT": feed},
        journal=journal,
    )

    orchestrator = Orchestrator(
        market_monitor=MarketMonitorAgent(symbols=["BTC/USDT"], feeds={"BTC/USDT": feed}),
        prediction=prediction_agent,
        risk=RiskAgent(max_position_size=0.5),
        execution=ExecutionAgent(portfolio=portfolio_manager, exchange=exchange),
        portfolio=PortfolioAgent(portfolio=portfolio_manager, journal=journal),
        journal=journal,
        strategy_manager=strategy_manager,
        tournament_interval_seconds=None,  # only exercise the warm-start path here
    )

    asyncio.run(orchestrator.run_forever(max_cycles=1))

    assert journal.latest_tournament_event("BTC/USDT") is not None
    assert "BTC/USDT" in strategy_manager.last_tournament


def test_run_supervised_catches_a_raising_sync_callable_and_alerts(tmp_path):
    from trading_assistant.agents.alerting import AlertManager

    orchestrator, _, journal, _ = _build_orchestrator(
        tmp_path, model=DummyClassifier(up_probability=0.5), closes=[100.0] * 20
    )
    orchestrator.alert_manager = AlertManager(journal=journal)

    def boom():
        raise RuntimeError("simulated background loop failure")

    asyncio.run(orchestrator._run_supervised("retrain", boom))  # must not raise

    alerts = journal.recent_alerts()
    assert any(a["category"] == "background_loop_failed" and a["severity"] == "critical" for a in alerts)


def test_run_supervised_awaits_an_async_callable_normally(tmp_path):
    orchestrator, _, journal, _ = _build_orchestrator(
        tmp_path, model=DummyClassifier(up_probability=0.5), closes=[100.0] * 20
    )
    calls = []

    async def works():
        calls.append(1)

    asyncio.run(orchestrator._run_supervised("retrain", works))

    assert calls == [1]
    assert journal.recent_alerts() == []


def test_run_supervised_silent_without_alert_manager_configured(tmp_path):
    """Backward compatible: alert_manager defaults to None - a failure must
    still be swallowed (the whole point), just not alerted externally."""
    orchestrator, _, journal, _ = _build_orchestrator(
        tmp_path, model=DummyClassifier(up_probability=0.5), closes=[100.0] * 20
    )
    assert orchestrator.alert_manager is None

    def boom():
        raise RuntimeError("simulated failure")

    asyncio.run(orchestrator._run_supervised("tournament", boom))  # must not raise


def test_retrain_loop_survives_a_raising_iteration_and_retries(tmp_path):
    """The core fix: a background loop used to die PERMANENTLY the moment
    one iteration raised - nothing supervised the asyncio.Task, so
    retraining would silently stop forever. Now the loop's own while-True
    must still be alive (and try again) after a failing iteration."""
    orchestrator, _, journal, _ = _build_orchestrator(
        tmp_path, model=DummyClassifier(up_probability=0.5), closes=[100.0] * 20
    )
    orchestrator.retrain_interval_seconds = 0
    call_count = {"n": 0}

    async def raising_retrain_all_symbols():
        call_count["n"] += 1
        raise RuntimeError("simulated retrain failure")

    orchestrator._retrain_all_symbols = raising_retrain_all_symbols

    async def run_two_iterations():
        task = asyncio.create_task(orchestrator._retrain_loop())
        await asyncio.sleep(0.3)  # long enough for several sleep(0) iterations
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run_two_iterations())

    # the loop kept calling _retrain_all_symbols repeatedly, never died on
    # the first failure the way it used to before this fix
    assert call_count["n"] >= 2


def test_retrain_all_symbols_stops_early_once_shutdown_is_requested(tmp_path):
    """Regression: cancelling the asyncio.Task wrapping a per-symbol retrain
    pass does NOT stop already-running work inside asyncio.to_thread - the
    underlying thread keeps running regardless, so a full pass (15-25
    minutes across a large universe) used to be the real worst-case
    shutdown delay. _retrain_all_symbols must check between symbols and
    stop STARTING new fetches once shutdown is requested - the fetch phase
    is per-symbol (see _retrain_all_symbols' own docstring on why:
    LearningAgent.retrain_all batches the actual promotion decisions
    together for Benjamini-Hochberg correction, but data-fetching stays
    isolated per symbol)."""
    orchestrator, _, journal, _ = _build_orchestrator(
        tmp_path, model=DummyClassifier(up_probability=0.5), closes=[100.0] * 20
    )
    orchestrator.market_monitor.symbols = ["A/USDT", "B/USDT", "C/USDT"]
    processed = []

    def fake_fetch_ohlcv_for_retrain(symbol, timeframe=None):  # sync - called via asyncio.to_thread, like the real one
        processed.append(symbol)
        orchestrator._shutdown_event.set()  # shutdown requested mid-pass, after the first symbol
        return None

    orchestrator.learning = types.SimpleNamespace(fetch_ohlcv_for_retrain=fake_fetch_ohlcv_for_retrain)

    asyncio.run(orchestrator._retrain_all_symbols())

    assert processed == ["A/USDT"]  # stopped before B/USDT and C/USDT


def test_run_tournament_all_symbols_stops_early_once_shutdown_is_requested(tmp_path):
    orchestrator, _, journal, _ = _build_orchestrator(
        tmp_path, model=DummyClassifier(up_probability=0.5), closes=[100.0] * 20
    )
    orchestrator.market_monitor.symbols = ["A/USDT", "B/USDT", "C/USDT"]
    processed = []

    def fake_run_tournament_from_live(symbol, timeframe=None):
        processed.append(symbol)
        orchestrator._shutdown_event.set()

    orchestrator.strategy_manager = types.SimpleNamespace(run_tournament_from_live=fake_run_tournament_from_live)

    asyncio.run(orchestrator._run_tournament_all_symbols())

    assert processed == ["A/USDT"]


def test_retention_loop_prunes_old_records_on_its_own_cadence(tmp_path):
    orchestrator, _, journal, _ = _build_orchestrator(
        tmp_path, model=DummyClassifier(up_probability=0.5), closes=[100.0] * 20
    )
    orchestrator.retention_interval_seconds = 0
    orchestrator.retention_days = 180
    journal.record_decision(
        symbol="BTC/USDT", signal="HOLD", risk_approved=True, action="no_action",
        timestamp="2020-01-01T00:00:00+00:00",
    )

    async def run_one_iteration():
        task = asyncio.create_task(orchestrator._retention_loop())
        await asyncio.sleep(0.2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run_one_iteration())

    assert journal.recent_decisions(limit=10) == []


def test_retention_loop_survives_a_raising_iteration_and_retries(tmp_path):
    orchestrator, _, journal, _ = _build_orchestrator(
        tmp_path, model=DummyClassifier(up_probability=0.5), closes=[100.0] * 20
    )
    orchestrator.retention_interval_seconds = 0
    call_count = {"n": 0}

    def raising_prune(*args, **kwargs):
        call_count["n"] += 1
        raise RuntimeError("simulated retention failure")

    orchestrator._prune_old_records = raising_prune

    async def run_two_iterations():
        task = asyncio.create_task(orchestrator._retention_loop())
        await asyncio.sleep(0.3)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run_two_iterations())

    assert call_count["n"] >= 2


def test_run_forever_starts_and_cancels_the_retention_task(tmp_path):
    """Regression guard for the wiring itself: retention_interval_seconds
    set (and journal configured) must actually start a retention_task
    inside run_forever, not just have a loop method that's never called."""
    orchestrator, _, journal, _ = _build_orchestrator(
        tmp_path, model=DummyClassifier(up_probability=0.5), closes=[100.0] * 20
    )
    orchestrator.retention_interval_seconds = 9999  # long enough it won't fire during this test
    journal.record_decision(
        symbol="BTC/USDT", signal="HOLD", risk_approved=True, action="no_action",
        timestamp="2020-01-01T00:00:00+00:00",
    )

    asyncio.run(orchestrator.run_forever(max_cycles=1, warm_start=False))

    # the old seeded row is untouched because the interval never elapsed -
    # this just proves run_forever didn't crash wiring in a fourth
    # background task (the second row is run_cycle's own decision journal)
    timestamps = [d["timestamp"] for d in journal.recent_decisions(limit=10)]
    assert "2020-01-01T00:00:00+00:00" in timestamps


def test_tournament_loop_builds_stability_history_without_touching_execution(tmp_path):
    """The tournament loop must run on its own schedule and build up
    StrategyManager's winner_history, but never influence execution or
    portfolio state - it's purely observational until that stability
    track record is deliberately reviewed and acted on."""
    from trading_assistant.agents.strategy_manager import StrategyCandidate, StrategyManager
    from trading_assistant.strategies.implementations import SMACrossoverStrategy

    closes = [100.0 + i * 0.5 for i in range(300)]
    feed = FakeFeed(closes)
    portfolio_manager = PortfolioManager(initial_capital=10000.0)
    journal = Journal(db_path=str(tmp_path / "journal.db"))
    exchange = DummyExchange()
    prediction_agent = PredictionAgent(model_dir=tmp_path, auto_load=False)

    strategy_manager = StrategyManager(
        candidates=[StrategyCandidate("sma_crossover", "rule", SMACrossoverStrategy)],
        train_window_bars=60,
        test_window_bars=20,
        min_folds_required=2,
        feeds={"BTC/USDT": feed},
        journal=journal,
    )

    orchestrator = Orchestrator(
        market_monitor=MarketMonitorAgent(symbols=["BTC/USDT"], feeds={"BTC/USDT": feed}),
        prediction=prediction_agent,
        risk=RiskAgent(max_position_size=0.5),
        execution=ExecutionAgent(portfolio=portfolio_manager, exchange=exchange),
        portfolio=PortfolioAgent(portfolio=portfolio_manager, journal=journal),
        journal=journal,
        strategy_manager=strategy_manager,
        tournament_interval_seconds=0,
    )

    async def run_one_iteration():
        task = asyncio.create_task(orchestrator._tournament_loop())
        await asyncio.sleep(0.5)  # let the loop's sleep(0) + one tournament run actually execute
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run_one_iteration())

    assert strategy_manager.winner_history_for("BTC/USDT", "trending_up") != []
    assert portfolio_manager.positions == {}  # execution/prediction never touched
    assert journal.latest_tournament_event("BTC/USDT") is not None


def test_tournament_warm_start_flags_underperforming_challenger_via_alert(tmp_path):
    """Underperforming-candidate visibility: after each tournament run, the
    orchestrator must compare every paper-traded challenger against the
    real champion's live Sharpe and raise an alert for anyone trailing it
    with enough trades to be meaningful - otherwise a chronically losing
    candidate keeps getting re-evaluated forever with no signal it's dead
    weight worth an operator's attention."""
    from trading_assistant.agents.alerting import AlertManager
    from trading_assistant.agents.champion_challenger import ChampionChallengerAgent
    from trading_assistant.agents.strategy_manager import StrategyCandidate, StrategyManager
    from trading_assistant.strategies.implementations import SMACrossoverStrategy

    closes = [100.0 + i * 0.5 for i in range(300)]
    feed = FakeFeed(closes)
    portfolio_manager = PortfolioManager(initial_capital=10000.0)
    journal = Journal(db_path=str(tmp_path / "journal.db"))
    exchange = DummyExchange()
    prediction_agent = PredictionAgent(model_dir=tmp_path, auto_load=False)
    alert_manager = AlertManager(journal=journal)

    strategy_manager = StrategyManager(
        candidates=[StrategyCandidate("sma_crossover", "rule", SMACrossoverStrategy)],
        train_window_bars=60,
        test_window_bars=20,
        min_folds_required=2,
        feeds={"BTC/USDT": feed},
        journal=journal,
    )
    champion_challenger = ChampionChallengerAgent(
        strategy_manager=strategy_manager, journal=journal, min_trades_to_compare=1, alert_manager=alert_manager
    )
    # Seed a challenger with enough trades and a clearly negative equity
    # curve (Sharpe well below the flat, untraded champion's Sharpe of 0) -
    # simulating an already-accumulated losing paper track record without
    # needing to actually run process_cycle repeatedly.
    losing_portfolio = champion_challenger._get_or_create_portfolio("BTC/USDT", "sma_crossover")
    losing_portfolio.equity_curve = [10000.0, 9500.0, 9000.0, 8500.0, 8000.0]
    losing_portfolio.trade_history = [{"pnl": -100.0}] * 5

    orchestrator = Orchestrator(
        market_monitor=MarketMonitorAgent(symbols=["BTC/USDT"], feeds={"BTC/USDT": feed}),
        prediction=prediction_agent,
        risk=RiskAgent(max_position_size=0.5),
        execution=ExecutionAgent(portfolio=portfolio_manager, exchange=exchange),
        portfolio=PortfolioAgent(portfolio=portfolio_manager, journal=journal),
        journal=journal,
        strategy_manager=strategy_manager,
        champion_challenger=champion_challenger,
        tournament_interval_seconds=None,
    )

    asyncio.run(orchestrator._run_tournament_all_symbols())

    alerts = journal.recent_alerts()
    underperform_alerts = [a for a in alerts if a["category"] == "challenger_underperforming"]
    assert len(underperform_alerts) == 1
    assert "sma_crossover" in underperform_alerts[0]["message"]


def test_drive_live_trading_routes_champion_to_real_execution_and_challengers_paper_trade(tmp_path):
    """The core champion-challenger contract: with drive_live_trading=True,
    the tournament's regime winner drives the REAL execution path (the
    exchange gets touched), while every other candidate accumulates its
    own separate paper track record and never touches the exchange."""
    from trading_assistant.agents.champion_challenger import ChampionChallengerAgent
    from trading_assistant.agents.strategy_manager import RegimeSelection, StrategyCandidate, StrategyManager

    class AlwaysBuy:
        def generate_signal(self, data):
            return Signal.BUY

    class AlwaysHold:
        def generate_signal(self, data):
            return Signal.HOLD

    closes = [100.0 + i * 0.5 for i in range(300)]  # empirically "trending_up" for this fixture (see test above)
    feed = FakeFeed(closes)
    portfolio_manager = PortfolioManager(initial_capital=10000.0)
    journal = Journal(db_path=str(tmp_path / "journal.db"))
    exchange = DummyExchange()
    prediction_agent = PredictionAgent(model_dir=tmp_path, auto_load=False)

    strategy_manager = StrategyManager(
        candidates=[
            StrategyCandidate("champion_strategy", "rule", AlwaysBuy),
            StrategyCandidate("challenger_strategy", "rule", AlwaysHold),
        ],
        train_window_bars=60,
        test_window_bars=20,
        min_folds_required=2,
        feeds={"BTC/USDT": feed},
        journal=journal,
    )
    strategy_manager.run_tournament_from_live("BTC/USDT", feed=feed)  # populates live_instances
    # force the champion designation directly so the test is deterministic,
    # independent of whether AlwaysBuy happens to clear the promotion bar
    strategy_manager.last_tournament["BTC/USDT"].winners["trending_up"] = RegimeSelection(
        regime="trending_up", winner="champion_strategy", reason="forced for test"
    )

    champion_challenger = ChampionChallengerAgent(strategy_manager=strategy_manager, journal=journal)

    orchestrator = Orchestrator(
        market_monitor=MarketMonitorAgent(symbols=["BTC/USDT"], feeds={"BTC/USDT": feed}),
        prediction=prediction_agent,
        risk=RiskAgent(max_position_size=0.5),
        execution=ExecutionAgent(portfolio=portfolio_manager, exchange=exchange),
        portfolio=PortfolioAgent(portfolio=portfolio_manager, journal=journal),
        journal=journal,
        strategy_manager=strategy_manager,
        champion_challenger=champion_challenger,
        drive_live_trading=True,
    )

    orchestrator.run_cycle()

    assert "BTC/USDT" in portfolio_manager.positions  # champion (AlwaysBuy) traded for real
    assert exchange.buy_orders  # the real exchange was touched by the champion

    challenger_portfolio = champion_challenger._portfolios["BTC/USDT"]["challenger_strategy"]
    assert "BTC/USDT" not in challenger_portfolio.positions  # AlwaysHold never opens anything, correctly

    decisions = journal.recent_decisions(limit=10)
    champion_decision = next(d for d in decisions if d["details"].get("champion") == "champion_strategy")
    assert champion_decision["signal"] == "BUY"
    assert champion_decision["action"] == "opened_position"


def test_paper_trading_runs_even_when_drive_live_trading_is_off(tmp_path):
    """Paper trading must not be gated behind drive_live_trading: while the
    tournament is still observation-only (the default, safer state), nothing
    else is tracking the tournament's candidates for real, so every
    candidate - including whichever one the tournament currently favors -
    should still accumulate a paper track record. Real execution must stay
    driven by the original prediction/learning path throughout."""
    from trading_assistant.agents.champion_challenger import ChampionChallengerAgent
    from trading_assistant.agents.strategy_manager import RegimeSelection, StrategyCandidate, StrategyManager

    class AlwaysBuy:
        def generate_signal(self, data):
            return Signal.BUY

    class AlwaysHold:
        def generate_signal(self, data):
            return Signal.HOLD

    closes = [100.0 + i * 0.5 for i in range(300)]
    feed = FakeFeed(closes)
    portfolio_manager = PortfolioManager(initial_capital=10000.0)
    journal = Journal(db_path=str(tmp_path / "journal.db"))
    exchange = DummyExchange()
    prediction_agent = PredictionAgent(model_dir=tmp_path, auto_load=False)

    strategy_manager = StrategyManager(
        candidates=[
            StrategyCandidate("would_be_champion", "rule", AlwaysBuy),
            StrategyCandidate("other_candidate", "rule", AlwaysHold),
        ],
        train_window_bars=60,
        test_window_bars=20,
        min_folds_required=2,
        feeds={"BTC/USDT": feed},
        journal=journal,
    )
    strategy_manager.run_tournament_from_live("BTC/USDT", feed=feed)
    strategy_manager.last_tournament["BTC/USDT"].winners["trending_up"] = RegimeSelection(
        regime="trending_up", winner="would_be_champion", reason="forced for test"
    )

    champion_challenger = ChampionChallengerAgent(strategy_manager=strategy_manager, journal=journal)

    orchestrator = Orchestrator(
        market_monitor=MarketMonitorAgent(symbols=["BTC/USDT"], feeds={"BTC/USDT": feed}),
        prediction=prediction_agent,
        risk=RiskAgent(max_position_size=0.5),
        execution=ExecutionAgent(portfolio=portfolio_manager, exchange=exchange),
        portfolio=PortfolioAgent(portfolio=portfolio_manager, journal=journal),
        journal=journal,
        strategy_manager=strategy_manager,
        champion_challenger=champion_challenger,
        drive_live_trading=False,  # the default, safer state
    )

    orchestrator.run_cycle()

    # real execution untouched - still driven by prediction_agent, not the tournament
    assert not exchange.buy_orders

    # but BOTH candidates paper-traded, including the one the tournament favors
    portfolios = champion_challenger._portfolios["BTC/USDT"]
    assert "would_be_champion" in portfolios
    assert "other_candidate" in portfolios
    assert "BTC/USDT" in portfolios["would_be_champion"].positions  # AlwaysBuy opened a paper long

    paper_snapshots = journal.recent_paper_equity_snapshots(symbol="BTC/USDT")
    assert len(paper_snapshots) == 2  # one per candidate this cycle
    assert {s["candidate"] for s in paper_snapshots} == {"would_be_champion", "other_candidate"}


class StopCapableExchange(DummyExchange):
    """Adds STOP_LOSS_LIMIT order placement/cancellation/atomic-replace on
    top of the base DummyExchange - needed by trailing-stop and
    partial-profit tests. edit_order mimics Binance's real cancelReplace
    contract (see protective_stops.replace_protective_stop): it records
    both what was replaced and the new order's parameters in one call,
    since that's what actually happens on the exchange side."""

    def __init__(self):
        super().__init__()
        self.stop_orders = []
        self.cancelled_orders = []
        self.edit_calls = []
        # Starts at 100, distinct from the "1" fixtures hardcode as the
        # pre-existing resting stop's id - avoids a same-looking-but-
        # coincidental id collision (e.g. after a failed edit_order call
        # that never consumed a ticket) masking whether a genuinely NEW
        # order was actually placed.
        self._next_id = 100
        # When set, edit_order raises instead of succeeding - simulates the
        # exchange rejecting the replacement (e.g. InsufficientFunds).
        self.edit_order_should_fail = False
        # When set, create_order raises instead of succeeding - simulates a
        # fresh protective-stop placement being rejected (distinct from
        # edit_order_should_fail, which is specifically the atomic-replace
        # path used by the trailing-stop updater).
        self.create_order_should_fail = False

    def create_order(self, symbol, type_, side, amount, price=None, params=None):
        if self.create_order_should_fail:
            raise ConnectionError("simulated stop placement failure")
        order_id = str(self._next_id)
        self._next_id += 1
        self.stop_orders.append((symbol, type_, side, amount, price, params or {}))
        return {"id": order_id, "symbol": symbol, "type": type_}

    def cancel_order(self, order_id, symbol):
        self.cancelled_orders.append((order_id, symbol))

    def edit_order(self, id, symbol, type_, side, amount, price=None, params=None):
        self.edit_calls.append((id, symbol, type_, side, amount, price, params or {}))
        if self.edit_order_should_fail:
            raise ConnectionError("simulated exchange rejection (e.g. InsufficientFunds)")
        order_id = str(self._next_id)
        self._next_id += 1
        # A real cancelReplace also cancels the old order server-side -
        # reflected here so tests can assert on it via cancelled_orders
        # the same way they already do for the plain cancel_order path.
        self.cancelled_orders.append((id, symbol))
        self.stop_orders.append((symbol, type_, side, amount, price, params or {}))
        return {"id": order_id, "symbol": symbol, "type": type_}


def _build_position_orchestrator(tmp_path, exchange, **orchestrator_kwargs):
    """A minimal orchestrator with an already-open BTC/USDT position
    protected by a resting stop - the common setup every trailing-stop /
    partial-profit test starts from."""
    portfolio_manager = PortfolioManager(initial_capital=10000.0)
    journal = Journal(db_path=str(tmp_path / "journal.db"))
    prediction_agent = PredictionAgent(model_dir=tmp_path, auto_load=False)
    prediction_agent.set_model("BTC/USDT", DummyClassifier(up_probability=0.5), feature_columns=["close"])

    orchestrator = Orchestrator(
        market_monitor=MarketMonitorAgent(symbols=["BTC/USDT"], feeds={"BTC/USDT": FakeFeed([100.0] * 20)}),
        prediction=prediction_agent,
        risk=RiskAgent(max_position_size=0.5),
        execution=ExecutionAgent(portfolio=portfolio_manager, exchange=exchange, stop_loss_pct=0.02),
        portfolio=PortfolioAgent(portfolio=portfolio_manager, journal=journal),
        journal=journal,
        **orchestrator_kwargs,
    )
    portfolio_manager.open_position("BTC/USDT", price=100.0, quantity=10.0)
    portfolio_manager.positions["BTC/USDT"]["stop_order_id"] = "1"
    portfolio_manager.positions["BTC/USDT"]["stop_price"] = 98.0
    return orchestrator, portfolio_manager, journal


class TestTrailingStop:
    def test_raises_the_stop_as_price_advances(self, tmp_path):
        exchange = StopCapableExchange()
        orchestrator, portfolio_manager, _ = _build_position_orchestrator(
            tmp_path, exchange, trailing_stop_pct=0.05
        )

        orchestrator._update_trailing_stop("BTC/USDT", current_price=120.0)

        # new stop = 120 * (1 - 0.05) = 114, well above the original 98
        assert portfolio_manager.positions["BTC/USDT"]["stop_price"] == pytest.approx(114.0)
        # replaced atomically via edit_order, not a separate cancel+create
        assert len(exchange.edit_calls) == 1
        old_id, symbol, type_, side, amount, price, params = exchange.edit_calls[0]
        assert old_id == "1"
        assert symbol == "BTC/USDT"
        assert params["stopPrice"] == pytest.approx(114.0)
        assert params["cancelReplaceMode"] == "STOP_ON_FAILURE"
        assert exchange.cancelled_orders == [("1", "BTC/USDT")]
        assert len(exchange.stop_orders) == 1

    def test_does_not_move_the_stop_on_a_pullback(self, tmp_path):
        exchange = StopCapableExchange()
        orchestrator, portfolio_manager, _ = _build_position_orchestrator(
            tmp_path, exchange, trailing_stop_pct=0.05
        )
        orchestrator._update_trailing_stop("BTC/USDT", current_price=120.0)
        exchange.cancelled_orders.clear()
        exchange.stop_orders.clear()
        exchange.edit_calls.clear()

        orchestrator._update_trailing_stop("BTC/USDT", current_price=105.0)  # pulled back from 120

        assert portfolio_manager.positions["BTC/USDT"]["stop_price"] == pytest.approx(114.0)  # unchanged
        assert exchange.edit_calls == []
        assert exchange.cancelled_orders == []
        assert exchange.stop_orders == []

    def test_disabled_by_default(self, tmp_path):
        exchange = StopCapableExchange()
        orchestrator, portfolio_manager, _ = _build_position_orchestrator(tmp_path, exchange)  # trailing_stop_pct=None

        orchestrator._update_trailing_stop("BTC/USDT", current_price=200.0)

        assert portfolio_manager.positions["BTC/USDT"]["stop_price"] == 98.0  # unchanged
        assert exchange.stop_orders == []

    def test_no_open_position_is_a_noop(self, tmp_path):
        exchange = StopCapableExchange()
        orchestrator, portfolio_manager, _ = _build_position_orchestrator(
            tmp_path, exchange, trailing_stop_pct=0.05
        )
        portfolio_manager.positions.clear()

        orchestrator._update_trailing_stop("BTC/USDT", current_price=200.0)  # must not raise

        assert exchange.stop_orders == []

    def test_failed_replacement_keeps_the_existing_stop_and_retries_next_cycle(self, tmp_path):
        """The core fix for the repeated trailing_stop_failed/position-left-
        unprotected-forever bug: a rejected replacement must leave the
        position exactly as protected as before (old stop_order_id/
        stop_price untouched, since the atomic cancelReplace guarantees
        the exchange never cancelled the old order either), and the very
        next cycle must retry rather than silently giving up forever."""
        from trading_assistant.agents.alerting import AlertManager

        exchange = StopCapableExchange()
        exchange.edit_order_should_fail = True
        # Same underlying SQLite file as the journal _build_position_orchestrator
        # constructs internally (tmp_path / "journal.db") - a separate Journal
        # instance pointed at the same file, so alerts recorded through this
        # one are visible via the journal object returned below.
        alert_manager = AlertManager(journal=Journal(db_path=str(tmp_path / "journal.db")))
        orchestrator, portfolio_manager, journal = _build_position_orchestrator(
            tmp_path, exchange, trailing_stop_pct=0.05, alert_manager=alert_manager
        )

        orchestrator._update_trailing_stop("BTC/USDT", current_price=120.0)

        position = portfolio_manager.positions["BTC/USDT"]
        assert position["stop_order_id"] == "1"  # unchanged - still the original resting stop
        assert position["stop_price"] == 98.0  # unchanged
        assert exchange.cancelled_orders == []  # STOP_ON_FAILURE - never touched

        alerts = journal.recent_alerts()
        assert any(a["category"] == "trailing_stop_raise_failed" and a["severity"] == "warning" for a in alerts)

        # Next cycle: still retries at (roughly) the same candidate price,
        # rather than the "if not stop_order_id: return" bug making this
        # position permanently unprotected after one failure.
        exchange.edit_order_should_fail = False
        orchestrator._update_trailing_stop("BTC/USDT", current_price=121.0)

        assert portfolio_manager.positions["BTC/USDT"]["stop_order_id"] != "1"
        assert portfolio_manager.positions["BTC/USDT"]["stop_price"] == pytest.approx(121.0 * 0.95)


class TestTrailingStopActivationThreshold:
    def test_does_not_trail_before_profit_clears_the_activation_threshold(self, tmp_path):
        exchange = StopCapableExchange()
        orchestrator, portfolio_manager, _ = _build_position_orchestrator(
            tmp_path, exchange, trailing_stop_pct=0.05, trailing_stop_activation_pct=0.10,
        )
        # Entry at 100.0 (see _build_position_orchestrator) - +5% profit is
        # below the 10% activation threshold, so trailing should not
        # engage at all, even though 105*(1-0.05)=99.75 is technically
        # below current price and would otherwise be a valid candidate.
        orchestrator._update_trailing_stop("BTC/USDT", current_price=105.0)

        assert portfolio_manager.positions["BTC/USDT"]["stop_price"] == 98.0  # unchanged
        assert exchange.edit_calls == []

    def test_trails_once_profit_clears_the_activation_threshold(self, tmp_path):
        exchange = StopCapableExchange()
        orchestrator, portfolio_manager, _ = _build_position_orchestrator(
            tmp_path, exchange, trailing_stop_pct=0.05, trailing_stop_activation_pct=0.10,
        )

        orchestrator._update_trailing_stop("BTC/USDT", current_price=115.0)  # +15%, clears the 10% threshold

        assert portfolio_manager.positions["BTC/USDT"]["stop_price"] == pytest.approx(115.0 * 0.95)

    def test_none_preserves_the_original_always_trail_behavior(self, tmp_path):
        exchange = StopCapableExchange()
        orchestrator, portfolio_manager, _ = _build_position_orchestrator(
            tmp_path, exchange, trailing_stop_pct=0.05,  # trailing_stop_activation_pct defaults to None
        )

        orchestrator._update_trailing_stop("BTC/USDT", current_price=105.0)

        assert portfolio_manager.positions["BTC/USDT"]["stop_price"] == pytest.approx(105.0 * 0.95)


class TestTrendExit:
    def test_closes_the_position_when_price_falls_below_the_sma(self, tmp_path):
        exchange = StopCapableExchange()
        orchestrator, portfolio_manager, journal = _build_position_orchestrator(
            tmp_path, exchange, trend_exit_sma_period=5,
        )
        close_series = pd.Series([100.0, 101.0, 102.0, 103.0, 104.0, 105.0])  # sma5 of last 5 = 103.0

        closed = orchestrator._maybe_trend_exit("BTC/USDT", current_price=100.0, close_series=close_series)

        assert closed is True
        assert "BTC/USDT" not in portfolio_manager.positions
        assert exchange.sell_orders == [("BTC/USDT", 10.0)]
        decisions = journal.recent_decisions(symbol="BTC/USDT", limit=5)
        assert any(d["signal"] == "TREND_EXIT" for d in decisions)

    def test_does_not_exit_when_price_is_still_above_the_sma(self, tmp_path):
        exchange = StopCapableExchange()
        orchestrator, portfolio_manager, _ = _build_position_orchestrator(
            tmp_path, exchange, trend_exit_sma_period=5,
        )
        close_series = pd.Series([100.0, 101.0, 102.0, 103.0, 104.0, 105.0])

        closed = orchestrator._maybe_trend_exit("BTC/USDT", current_price=110.0, close_series=close_series)

        assert closed is False
        assert "BTC/USDT" in portfolio_manager.positions
        assert exchange.sell_orders == []

    def test_disabled_by_default(self, tmp_path):
        exchange = StopCapableExchange()
        orchestrator, portfolio_manager, _ = _build_position_orchestrator(tmp_path, exchange)  # trend_exit_sma_period=None
        close_series = pd.Series([105.0, 104.0, 103.0, 102.0, 101.0, 100.0])

        closed = orchestrator._maybe_trend_exit("BTC/USDT", current_price=50.0, close_series=close_series)

        assert closed is False
        assert exchange.sell_orders == []

    def test_no_open_position_is_a_noop(self, tmp_path):
        exchange = StopCapableExchange()
        orchestrator, portfolio_manager, _ = _build_position_orchestrator(
            tmp_path, exchange, trend_exit_sma_period=5,
        )
        portfolio_manager.positions.clear()
        close_series = pd.Series([100.0, 101.0, 102.0, 103.0, 104.0, 105.0])

        closed = orchestrator._maybe_trend_exit("BTC/USDT", current_price=50.0, close_series=close_series)

        assert closed is False

    def test_none_close_series_is_a_noop(self, tmp_path):
        """The fast position-monitor loop may not have a cached snapshot
        yet for a symbol (e.g. right after warm start) - must not crash."""
        exchange = StopCapableExchange()
        orchestrator, portfolio_manager, _ = _build_position_orchestrator(
            tmp_path, exchange, trend_exit_sma_period=5,
        )

        closed = orchestrator._maybe_trend_exit("BTC/USDT", current_price=50.0, close_series=None)

        assert closed is False


class TestPartialProfitTaking:
    def test_sells_the_configured_fraction_once_threshold_crossed(self, tmp_path):
        exchange = StopCapableExchange()
        orchestrator, portfolio_manager, journal = _build_position_orchestrator(
            tmp_path, exchange, partial_profit_pct=0.10, partial_profit_sell_fraction=0.5
        )

        orchestrator._maybe_take_partial_profit("BTC/USDT", current_price=115.0)  # +15%, above the 10% threshold

        assert exchange.sell_orders == [("BTC/USDT", 5.0)]
        assert portfolio_manager.positions["BTC/USDT"]["quantity"] == 5.0
        assert portfolio_manager.positions["BTC/USDT"]["partial_profit_taken"] is True
        trades = journal.recent_trades()
        assert trades[0]["side"] == "sell"
        assert trades[0]["quantity"] == 5.0

    def test_does_not_fire_below_threshold(self, tmp_path):
        exchange = StopCapableExchange()
        orchestrator, portfolio_manager, _ = _build_position_orchestrator(
            tmp_path, exchange, partial_profit_pct=0.10
        )

        orchestrator._maybe_take_partial_profit("BTC/USDT", current_price=105.0)  # only +5%

        assert exchange.sell_orders == []
        assert portfolio_manager.positions["BTC/USDT"]["quantity"] == 10.0

    def test_fires_at_most_once(self, tmp_path):
        exchange = StopCapableExchange()
        orchestrator, portfolio_manager, _ = _build_position_orchestrator(
            tmp_path, exchange, partial_profit_pct=0.10, partial_profit_sell_fraction=0.5
        )

        orchestrator._maybe_take_partial_profit("BTC/USDT", current_price=115.0)
        orchestrator._maybe_take_partial_profit("BTC/USDT", current_price=130.0)  # still above threshold

        assert exchange.sell_orders == [("BTC/USDT", 5.0)]  # only the first call actually sold

    def test_disabled_by_default(self, tmp_path):
        exchange = StopCapableExchange()
        orchestrator, portfolio_manager, _ = _build_position_orchestrator(tmp_path, exchange)

        orchestrator._maybe_take_partial_profit("BTC/USDT", current_price=200.0)

        assert exchange.sell_orders == []
        assert portfolio_manager.positions["BTC/USDT"]["quantity"] == 10.0

    def test_re_protects_the_remainder_with_a_fresh_stop(self, tmp_path):
        exchange = StopCapableExchange()
        orchestrator, portfolio_manager, _ = _build_position_orchestrator(
            tmp_path, exchange, partial_profit_pct=0.10, partial_profit_sell_fraction=0.5
        )

        orchestrator._maybe_take_partial_profit("BTC/USDT", current_price=115.0)

        assert exchange.cancelled_orders == [("1", "BTC/USDT")]
        assert len(exchange.stop_orders) == 1
        _, _, _, amount, _, params = exchange.stop_orders[0]
        assert amount == 5.0
        assert params["stopPrice"] == pytest.approx(98.0)  # the level the position already had

    def test_records_a_trade_audit_entry(self, tmp_path):
        exchange = StopCapableExchange()
        orchestrator, portfolio_manager, journal = _build_position_orchestrator(
            tmp_path, exchange, partial_profit_pct=0.10, partial_profit_sell_fraction=0.5
        )

        orchestrator._maybe_take_partial_profit("BTC/USDT", current_price=115.0)

        records = journal.recent_trade_audit(symbol="BTC/USDT")
        assert len(records) == 1
        assert records[0]["side"] == "long"
        assert records[0]["exit_price"] == 115.0
        assert records[0]["quantity"] == 5.0
        assert "partial profit" in records[0]["exit_reason"]
        # this fixture opened the position directly on the portfolio, not
        # through the orchestrator's journaled BUY path - entry context is
        # honestly None rather than fabricated, exit context is still real.
        assert records[0]["entry_price"] is None


class TestEmergencyFlattenAll:
    def test_closes_every_open_position_and_trips_the_circuit_breaker(self, tmp_path):
        exchange = StopCapableExchange()
        orchestrator, portfolio_manager, journal = _build_position_orchestrator(tmp_path, exchange)

        results = orchestrator.emergency_flatten_all("operator-invoked kill switch")

        assert "BTC/USDT" not in portfolio_manager.positions
        assert results["BTC/USDT"].action == "closed_position"
        assert orchestrator.risk.circuit_breaker.tripped is True
        assert "operator-invoked kill switch" in orchestrator.risk.circuit_breaker.trip_reason

    def test_journals_an_emergency_flatten_decision(self, tmp_path):
        exchange = StopCapableExchange()
        orchestrator, _, journal = _build_position_orchestrator(tmp_path, exchange)

        orchestrator.emergency_flatten_all("test reason")

        decisions = journal.recent_decisions()
        assert decisions[0]["signal"] == "EMERGENCY_FLATTEN"
        assert decisions[0]["action"] == "closed_position"
        assert decisions[0]["details"]["emergency"] is True

    def test_raises_a_critical_alert(self, tmp_path):
        from trading_assistant.agents.alerting import AlertManager

        exchange = StopCapableExchange()
        alert_manager = AlertManager(journal=Journal(db_path=str(tmp_path / "journal.db")))
        orchestrator, _, journal = _build_position_orchestrator(tmp_path, exchange, alert_manager=alert_manager)

        orchestrator.emergency_flatten_all("test reason")

        alerts = journal.recent_alerts()
        assert any(a["category"] == "emergency_flatten" and a["severity"] == "critical" for a in alerts)

    def test_records_a_trade_audit_entry(self, tmp_path):
        exchange = StopCapableExchange()
        orchestrator, portfolio_manager, journal = _build_position_orchestrator(tmp_path, exchange)

        orchestrator.emergency_flatten_all("test reason")

        records = journal.recent_trade_audit(symbol="BTC/USDT")
        assert len(records) == 1
        assert records[0]["side"] == "long"
        assert records[0]["quantity"] == 10.0
        assert "emergency flatten: test reason" in records[0]["exit_reason"]

    def test_no_open_positions_still_trips_the_breaker(self, tmp_path):
        """The operator's intent (halt everything now) must be honored
        even if nothing happens to be open at that exact moment."""
        exchange = StopCapableExchange()
        portfolio_manager = PortfolioManager(initial_capital=10000.0)
        journal = Journal(db_path=str(tmp_path / "journal.db"))
        prediction_agent = PredictionAgent(model_dir=tmp_path, auto_load=False)
        orchestrator = Orchestrator(
            market_monitor=MarketMonitorAgent(symbols=["BTC/USDT"], feeds={"BTC/USDT": FakeFeed([100.0] * 20)}),
            prediction=prediction_agent,
            risk=RiskAgent(max_position_size=0.5),
            execution=ExecutionAgent(portfolio=portfolio_manager, exchange=exchange),
            portfolio=PortfolioAgent(portfolio=portfolio_manager, journal=journal),
            journal=journal,
        )

        results = orchestrator.emergency_flatten_all("test reason")

        assert results == {}
        assert orchestrator.risk.circuit_breaker.tripped is True

    def test_already_tripped_breaker_does_not_change_the_original_reason(self, tmp_path):
        exchange = StopCapableExchange()
        orchestrator, _, _ = _build_position_orchestrator(tmp_path, exchange)
        orchestrator.risk.circuit_breaker.trip("original reason")

        orchestrator.emergency_flatten_all("kill switch reason")

        assert orchestrator.risk.circuit_breaker.trip_reason == "original reason"
        # flattening still proceeds even though the trip reason didn't change
        assert "BTC/USDT" not in orchestrator.portfolio.portfolio.positions

    def test_symbol_with_no_fresh_price_is_left_open_and_reported(self, tmp_path):
        """A feed failure at exactly the wrong moment must not silently
        pretend the position was closed - it needs to stay visibly open
        (and logged) for manual attention, not vanish from tracking."""
        exchange = StopCapableExchange()
        portfolio_manager = PortfolioManager(initial_capital=10000.0)
        journal = Journal(db_path=str(tmp_path / "journal.db"))
        prediction_agent = PredictionAgent(model_dir=tmp_path, auto_load=False)

        class FailingFeed:
            def fetch_ohlcv(self, limit=100):
                raise ConnectionError("simulated outage")

        orchestrator = Orchestrator(
            market_monitor=MarketMonitorAgent(symbols=["BTC/USDT"], feeds={"BTC/USDT": FailingFeed()}),
            prediction=prediction_agent,
            risk=RiskAgent(max_position_size=0.5),
            execution=ExecutionAgent(portfolio=portfolio_manager, exchange=exchange, stop_loss_pct=0.02),
            portfolio=PortfolioAgent(portfolio=portfolio_manager, journal=journal),
            journal=journal,
        )
        portfolio_manager.open_position("BTC/USDT", price=100.0, quantity=10.0)

        results = orchestrator.emergency_flatten_all("test reason")

        assert results == {}
        assert "BTC/USDT" in portfolio_manager.positions  # left open, not silently dropped
        assert orchestrator.risk.circuit_breaker.tripped is True


class TestFlattenUnexplainedPositions:
    def test_sells_a_fully_unexplained_position(self, tmp_path):
        exchange = StopCapableExchange()
        orchestrator, portfolio_manager, journal = _build_position_orchestrator(tmp_path, exchange)
        portfolio_manager.positions["BTC/USDT"]["unexplained_quantity"] = 10.0  # fully unexplained

        results = orchestrator.flatten_unexplained_positions("dashboard cleanup")

        assert "BTC/USDT" not in portfolio_manager.positions
        assert results["BTC/USDT"].action == "closed_position"

    def test_does_not_trip_the_circuit_breaker(self, tmp_path):
        """The core difference from emergency_flatten_all: this is
        bookkeeping cleanup, not a risk event, and must never halt normal
        trading."""
        exchange = StopCapableExchange()
        orchestrator, portfolio_manager, _ = _build_position_orchestrator(tmp_path, exchange)
        portfolio_manager.positions["BTC/USDT"]["unexplained_quantity"] = 10.0

        orchestrator.flatten_unexplained_positions("dashboard cleanup")

        assert orchestrator.risk.circuit_breaker.tripped is False

    def test_leaves_a_genuinely_explained_position_untouched(self, tmp_path):
        """The core safety property: a real, strategy-opened position
        (unexplained_quantity absent or 0) must never be sold by this
        cleanup method - only fully-unexplained (faucet-style) balances."""
        exchange = StopCapableExchange()
        orchestrator, portfolio_manager, _ = _build_position_orchestrator(tmp_path, exchange)
        # no unexplained_quantity set - a genuine strategy-opened position

        results = orchestrator.flatten_unexplained_positions("dashboard cleanup")

        assert results == {}
        assert "BTC/USDT" in portfolio_manager.positions

    def test_leaves_a_partially_explained_position_untouched(self, tmp_path):
        """A MIXED position (some real, some unexplained) is not "fully
        unexplained" - selling it would liquidate real strategy capital
        too, which this cleanup method must never do."""
        exchange = StopCapableExchange()
        orchestrator, portfolio_manager, _ = _build_position_orchestrator(tmp_path, exchange)
        portfolio_manager.positions["BTC/USDT"]["unexplained_quantity"] = 4.0  # only 4 of 10 unexplained

        results = orchestrator.flatten_unexplained_positions("dashboard cleanup")

        assert results == {}
        assert "BTC/USDT" in portfolio_manager.positions

    def test_nets_to_zero_capital_and_pnl_impact(self, tmp_path):
        """Confirms this reuses close_position's own accounting fix -
        liquidating a fully-unexplained position must not inject phantom
        capital or realized PnL, matching the exact fix made earlier."""
        exchange = StopCapableExchange()
        orchestrator, portfolio_manager, journal = _build_position_orchestrator(tmp_path, exchange)
        capital_before = portfolio_manager.capital
        portfolio_manager.positions["BTC/USDT"]["unexplained_quantity"] = 10.0

        orchestrator.flatten_unexplained_positions("dashboard cleanup")

        assert portfolio_manager.capital == pytest.approx(capital_before)
        trades = journal.recent_trade_audit(symbol="BTC/USDT")
        assert trades[0]["realized_pnl"] == pytest.approx(0.0)

    def test_journals_a_flatten_unexplained_decision(self, tmp_path):
        exchange = StopCapableExchange()
        orchestrator, portfolio_manager, journal = _build_position_orchestrator(tmp_path, exchange)
        portfolio_manager.positions["BTC/USDT"]["unexplained_quantity"] = 10.0

        orchestrator.flatten_unexplained_positions("dashboard cleanup")

        decisions = journal.recent_decisions()
        assert decisions[0]["signal"] == "FLATTEN_UNEXPLAINED"
        assert decisions[0]["action"] == "closed_position"
        assert decisions[0]["details"]["unexplained_cleanup"] is True

    def test_no_unexplained_positions_is_a_clean_no_op(self, tmp_path):
        exchange = StopCapableExchange()
        orchestrator, portfolio_manager, _ = _build_position_orchestrator(tmp_path, exchange)

        results = orchestrator.flatten_unexplained_positions("dashboard cleanup")

        assert results == {}
        assert orchestrator.risk.circuit_breaker.tripped is False
        assert "BTC/USDT" in portfolio_manager.positions


class TestUnprotectedPositionAlert:
    """Regression coverage for a real gap found in this system: a
    protective stop failing to place/re-place used to be only a log line
    ("... UNPROTECTED") nobody watches in real time - a genuinely
    unprotected real position (direct capital-at-risk) must raise a loud
    alert instead."""

    def test_raises_a_critical_alert_when_stop_order_placed_is_false(self, tmp_path):
        from trading_assistant.agents.alerting import AlertManager
        from trading_assistant.agents.execution import ExecutionResult

        exchange = StopCapableExchange()
        journal = Journal(db_path=str(tmp_path / "journal.db"))
        alert_manager = AlertManager(journal=journal)
        orchestrator, _, _ = _build_position_orchestrator(tmp_path, exchange, alert_manager=alert_manager)
        result = ExecutionResult(
            symbol="BTC/USDT", action="opened_position", price=100.0, quantity=1.0, stop_order_placed=False
        )

        orchestrator._maybe_alert_unprotected_position("BTC/USDT", result)

        alerts = journal.recent_alerts()
        assert len(alerts) == 1
        assert alerts[0]["category"] == "position_unprotected"
        assert alerts[0]["severity"] == "critical"
        assert "BTC/USDT" in alerts[0]["message"]

    def test_does_not_alert_when_stop_order_placed_is_true(self, tmp_path):
        from trading_assistant.agents.alerting import AlertManager
        from trading_assistant.agents.execution import ExecutionResult

        exchange = StopCapableExchange()
        journal = Journal(db_path=str(tmp_path / "journal.db"))
        alert_manager = AlertManager(journal=journal)
        orchestrator, _, _ = _build_position_orchestrator(tmp_path, exchange, alert_manager=alert_manager)
        result = ExecutionResult(
            symbol="BTC/USDT", action="opened_position", price=100.0, quantity=1.0, stop_order_placed=True
        )

        orchestrator._maybe_alert_unprotected_position("BTC/USDT", result)

        assert journal.recent_alerts() == []

    def test_does_not_alert_when_stop_order_placed_is_none(self, tmp_path):
        # None means "not applicable" (no exchange, no stop configured, or
        # no open position left to protect) - not a failure, must stay silent.
        from trading_assistant.agents.alerting import AlertManager
        from trading_assistant.agents.execution import ExecutionResult

        exchange = StopCapableExchange()
        journal = Journal(db_path=str(tmp_path / "journal.db"))
        alert_manager = AlertManager(journal=journal)
        orchestrator, _, _ = _build_position_orchestrator(tmp_path, exchange, alert_manager=alert_manager)
        result = ExecutionResult(
            symbol="BTC/USDT", action="closed_position", price=100.0, quantity=1.0, stop_order_placed=None
        )

        orchestrator._maybe_alert_unprotected_position("BTC/USDT", result)

        assert journal.recent_alerts() == []

    def test_does_nothing_without_an_alert_manager_configured(self, tmp_path):
        from trading_assistant.agents.execution import ExecutionResult

        exchange = StopCapableExchange()
        orchestrator, _, _ = _build_position_orchestrator(tmp_path, exchange)  # no alert_manager
        result = ExecutionResult(
            symbol="BTC/USDT", action="opened_position", price=100.0, quantity=1.0, stop_order_placed=False
        )

        orchestrator._maybe_alert_unprotected_position("BTC/USDT", result)  # must not raise

    def test_partial_profit_taking_alerts_when_reprotection_fails(self, tmp_path):
        # End-to-end: a real partial-profit sell whose re-protection fails
        # must surface as a real alert, not just a log line.
        from trading_assistant.agents.alerting import AlertManager

        exchange = StopCapableExchange()
        journal = Journal(db_path=str(tmp_path / "journal.db"))
        alert_manager = AlertManager(journal=journal)
        orchestrator, portfolio_manager, _ = _build_position_orchestrator(
            tmp_path, exchange, partial_profit_pct=0.10, partial_profit_sell_fraction=0.5,
            alert_manager=alert_manager,
        )
        exchange.create_order_should_fail = True

        orchestrator._maybe_take_partial_profit("BTC/USDT", current_price=115.0)

        assert portfolio_manager.positions["BTC/USDT"]["quantity"] == 5.0  # the sell itself still went through
        alerts = journal.recent_alerts()
        assert len(alerts) == 1
        assert alerts[0]["category"] == "position_unprotected"

    def test_partial_profit_taking_does_not_alert_when_reprotection_succeeds(self, tmp_path):
        from trading_assistant.agents.alerting import AlertManager

        exchange = StopCapableExchange()
        journal = Journal(db_path=str(tmp_path / "journal.db"))
        alert_manager = AlertManager(journal=journal)
        orchestrator, _, _ = _build_position_orchestrator(
            tmp_path, exchange, partial_profit_pct=0.10, partial_profit_sell_fraction=0.5,
            alert_manager=alert_manager,
        )

        orchestrator._maybe_take_partial_profit("BTC/USDT", current_price=115.0)

        assert journal.recent_alerts() == []


class TestUnsellableDustPositionAlert:
    """Regression coverage for a real incident: a held BTC/USDT position
    shrank below Binance's minimum order value, the sell was attempted
    anyway, was rejected 3 times in a row, and the circuit breaker tripped
    on a sizing mismatch instead of a genuine execution failure. See
    ExecutionAgent._sell's identical defensive check - this covers the
    alert half of the fix."""

    def test_raises_a_warning_alert_when_a_sell_is_skipped_for_being_below_minimum(self, tmp_path):
        from trading_assistant.agents.alerting import AlertManager
        from trading_assistant.agents.execution import ExecutionResult

        exchange = StopCapableExchange()
        journal = Journal(db_path=str(tmp_path / "journal.db"))
        alert_manager = AlertManager(journal=journal)
        orchestrator, _, _ = _build_position_orchestrator(tmp_path, exchange, alert_manager=alert_manager)
        result = ExecutionResult(
            symbol="BTC/USDT", action="no_action", price=100.0, quantity=0.0,
            error="position value $4.41 below exchange minimum $5.00 - cannot be sold at this size",
        )

        orchestrator._maybe_alert_unsellable_dust_position("BTC/USDT", result)

        alerts = journal.recent_alerts()
        assert len(alerts) == 1
        assert alerts[0]["category"] == "dust_position_unsellable"
        assert alerts[0]["severity"] == "warning"
        assert "BTC/USDT" in alerts[0]["message"]

    def test_does_not_alert_on_the_unrelated_nothing_to_sell_no_action(self, tmp_path):
        # "no_action" also covers the ordinary case of nothing actually
        # held to sell - that path leaves result.error as None, and must
        # never be confused with a genuine dust-skip.
        from trading_assistant.agents.alerting import AlertManager
        from trading_assistant.agents.execution import ExecutionResult

        exchange = StopCapableExchange()
        journal = Journal(db_path=str(tmp_path / "journal.db"))
        alert_manager = AlertManager(journal=journal)
        orchestrator, _, _ = _build_position_orchestrator(tmp_path, exchange, alert_manager=alert_manager)
        result = ExecutionResult(symbol="BTC/USDT", action="no_action", price=100.0, quantity=0.0)

        orchestrator._maybe_alert_unsellable_dust_position("BTC/USDT", result)

        assert journal.recent_alerts() == []

    def test_does_not_alert_on_a_successful_close(self, tmp_path):
        from trading_assistant.agents.alerting import AlertManager
        from trading_assistant.agents.execution import ExecutionResult

        exchange = StopCapableExchange()
        journal = Journal(db_path=str(tmp_path / "journal.db"))
        alert_manager = AlertManager(journal=journal)
        orchestrator, _, _ = _build_position_orchestrator(tmp_path, exchange, alert_manager=alert_manager)
        result = ExecutionResult(symbol="BTC/USDT", action="closed_position", price=100.0, quantity=10.0, pnl=5.0)

        orchestrator._maybe_alert_unsellable_dust_position("BTC/USDT", result)

        assert journal.recent_alerts() == []

    def test_does_nothing_without_an_alert_manager_configured(self, tmp_path):
        from trading_assistant.agents.execution import ExecutionResult

        exchange = StopCapableExchange()
        orchestrator, _, _ = _build_position_orchestrator(tmp_path, exchange)  # no alert_manager
        result = ExecutionResult(
            symbol="BTC/USDT", action="no_action", price=100.0, quantity=0.0,
            error="position value $4.41 below exchange minimum $5.00 - cannot be sold at this size",
        )

        orchestrator._maybe_alert_unsellable_dust_position("BTC/USDT", result)  # must not raise


class DustExchange(DummyExchange):
    """Reports a real exchange minimum order value, like MinNotionalExchange
    in test_agent_execution.py, and raises loudly if a sell is ever
    actually attempted - proving the fix stops the attempt before it
    reaches the exchange, not just handles the rejection gracefully."""

    def __init__(self, min_notional):
        super().__init__()
        self._min_notional = min_notional

    def market(self, symbol):
        return {"limits": {"cost": {"min": self._min_notional}}}

    def create_market_sell_order(self, symbol, quantity):
        raise AssertionError("a sell must never be attempted for a position below the exchange minimum")


def test_repeated_sell_signals_on_a_dust_position_no_longer_trip_the_circuit_breaker(tmp_path):
    """End-to-end reproduction of the real incident: a held position worth
    less than the exchange's minimum order value gets a SELL signal 3
    cycles in a row. Before the fix, each cycle's sell attempt was rejected
    by the exchange and counted as a circuit-breaker error, tripping it on
    the 3rd cycle. After the fix, the sell is never attempted at all, so it
    never counts against the circuit breaker's error budget."""
    closes = [100.0] * 20
    portfolio_manager = PortfolioManager(initial_capital=10000.0)
    journal = Journal(db_path=str(tmp_path / "journal.db"))
    exchange = DustExchange(min_notional=5.0)

    prediction_agent = PredictionAgent(model_dir=tmp_path, auto_load=False)
    prediction_agent.set_model("BTC/USDT", DummyClassifier(up_probability=0.1), feature_columns=["close"])

    risk_agent = RiskAgent(max_position_size=0.5, max_consecutive_errors=3)
    orchestrator = Orchestrator(
        market_monitor=MarketMonitorAgent(symbols=["BTC/USDT"], feeds={"BTC/USDT": FakeFeed(closes)}),
        prediction=prediction_agent,
        risk=risk_agent,
        execution=ExecutionAgent(portfolio=portfolio_manager, exchange=exchange, max_retries=1, backoff_seconds=0),
        portfolio=PortfolioAgent(portfolio=portfolio_manager, journal=journal),
        journal=journal,
    )
    # A dust-sized position: 0.00007 * 100.0 = $0.007, well below the $5 minimum.
    portfolio_manager.open_position("BTC/USDT", price=100.0, quantity=0.00007)

    for _ in range(3):
        orchestrator.run_cycle()

    assert risk_agent.circuit_breaker.tripped is False
    assert "BTC/USDT" in portfolio_manager.positions  # never closed - still stuck, as expected


class TestLivePaperFallback:
    """Regression coverage for the exact scenario a real user hit: real
    Binance API keys set, mode: live, but $0 (or below Binance's practical
    minimum order size) in the account. The main decision must paper-trade
    against real live data instead of either (a) attempting real orders
    that would just fail, or (b) going silent - the whole point is to keep
    gathering genuine evidence while waiting to be funded."""

    def _build_live_orchestrator(self, tmp_path, model, closes, real_free_balance, **orchestrator_kwargs):
        portfolio_manager = PortfolioManager(initial_capital=10000.0)
        journal = Journal(db_path=str(tmp_path / "journal.db"))

        class LiveExchange(DummyExchange):
            def fetch_balance(self):
                return {"USDT": {"free": real_free_balance, "total": real_free_balance}}

        exchange = LiveExchange()
        prediction_agent = PredictionAgent(model_dir=tmp_path, auto_load=False)
        prediction_agent.set_model("BTC/USDT", model, feature_columns=["close"])

        orchestrator = Orchestrator(
            market_monitor=MarketMonitorAgent(symbols=["BTC/USDT"], feeds={"BTC/USDT": FakeFeed(closes)}),
            prediction=prediction_agent,
            risk=RiskAgent(max_position_size=0.5),
            execution=ExecutionAgent(portfolio=portfolio_manager, exchange=exchange),
            portfolio=PortfolioAgent(portfolio=portfolio_manager, journal=journal),
            journal=journal,
            is_live=True,
            min_live_capital_usd=5.0,
            **orchestrator_kwargs,
        )
        return orchestrator, portfolio_manager, journal, exchange

    def test_paper_trades_when_real_capital_below_threshold(self, tmp_path):
        closes = [float(i) for i in range(1, 21)]
        orchestrator, portfolio_manager, journal, exchange = self._build_live_orchestrator(
            tmp_path, model=DummyClassifier(up_probability=0.9), closes=closes, real_free_balance=2.0,
        )

        orchestrator.run_cycle()

        # The REAL portfolio/exchange are completely untouched.
        assert "BTC/USDT" not in portfolio_manager.positions
        assert exchange.buy_orders == []
        assert portfolio_manager.capital == 2.0  # synced from the real balance, not spent

        # A paper position exists instead, in the dedicated fallback portfolio.
        paper_portfolio = orchestrator._live_paper_portfolio
        assert paper_portfolio is not None
        assert "BTC/USDT" in paper_portfolio.positions

        decisions = journal.recent_decisions()
        assert decisions[0]["signal"] == "BUY"
        assert decisions[0]["action"] == "opened_position"
        assert decisions[0]["details"]["paper"] is True
        assert decisions[0]["details"]["candidate"] == "live_fallback"

    def test_uses_real_execution_when_capital_clears_threshold(self, tmp_path):
        closes = [float(i) for i in range(1, 21)]
        orchestrator, portfolio_manager, journal, exchange = self._build_live_orchestrator(
            tmp_path, model=DummyClassifier(up_probability=0.9), closes=closes, real_free_balance=100.0,
        )

        orchestrator.run_cycle()

        assert "BTC/USDT" in portfolio_manager.positions  # real position opened
        assert exchange.buy_orders  # a real order was actually placed
        assert orchestrator._live_paper_portfolio is None  # paper fallback never even touched

        decisions = journal.recent_decisions()
        assert "paper" not in decisions[0]["details"]

    def test_paper_position_can_be_closed_by_a_later_sell_signal(self, tmp_path):
        closes = [float(i) for i in range(1, 21)]
        orchestrator, portfolio_manager, journal, exchange = self._build_live_orchestrator(
            tmp_path, model=DummyClassifier(up_probability=0.9), closes=closes, real_free_balance=2.0,
        )
        orchestrator.run_cycle()
        assert "BTC/USDT" in orchestrator._live_paper_portfolio.positions

        orchestrator.prediction.set_model("BTC/USDT", DummyClassifier(up_probability=0.1), feature_columns=["close"])
        orchestrator.run_cycle()

        assert "BTC/USDT" not in orchestrator._live_paper_portfolio.positions
        assert "BTC/USDT" not in portfolio_manager.positions  # real portfolio never had it to begin with
        assert exchange.sell_orders == []  # never a real order

        decisions = journal.recent_decisions()
        assert decisions[0]["action"] == "closed_position"
        assert decisions[0]["details"]["paper"] is True

    def test_run_cycle_syncs_capital_from_real_balance_every_cycle_when_live(self, tmp_path):
        closes = [float(i) for i in range(1, 21)]
        orchestrator, portfolio_manager, journal, exchange = self._build_live_orchestrator(
            tmp_path, model=DummyClassifier(up_probability=0.0), closes=closes, real_free_balance=3.0,
        )

        orchestrator.run_cycle()
        assert portfolio_manager.capital == 3.0

        exchange.fetch_balance = lambda: {"USDT": {"free": 9.0, "total": 9.0}}  # simulate a deposit
        orchestrator.run_cycle()

        assert portfolio_manager.capital == 9.0  # noticed without any restart

    def test_does_not_sync_or_paper_trade_when_not_live(self, tmp_path):
        # Backward compatible: testnet/paper (is_live defaults to False)
        # keeps the exact old behavior - no capital sync, no paper fallback,
        # even if real capital would otherwise be below the threshold.
        closes = [float(i) for i in range(1, 21)]
        portfolio_manager = PortfolioManager(initial_capital=2.0)  # below the 5.0 threshold, but not live
        journal = Journal(db_path=str(tmp_path / "journal.db"))

        class LiveExchange(DummyExchange):
            def fetch_balance(self):
                raise AssertionError("fetch_balance must not be called for capital sync when not live")

        exchange = LiveExchange()
        prediction_agent = PredictionAgent(model_dir=tmp_path, auto_load=False)
        prediction_agent.set_model("BTC/USDT", DummyClassifier(up_probability=0.9), feature_columns=["close"])

        orchestrator = Orchestrator(
            market_monitor=MarketMonitorAgent(symbols=["BTC/USDT"], feeds={"BTC/USDT": FakeFeed(closes)}),
            prediction=prediction_agent,
            risk=RiskAgent(max_position_size=0.5),
            execution=ExecutionAgent(portfolio=portfolio_manager, exchange=exchange),
            portfolio=PortfolioAgent(portfolio=portfolio_manager, journal=journal),
            journal=journal,
            # is_live defaults to False
        )

        orchestrator.run_cycle()  # must not raise (fetch_balance never called for capital sync)

        # Old testnet/paper behavior is fully unaffected: a real BUY still
        # fires normally (spending some of the real, unsynced capital) -
        # is_live=False just means nothing here ever touches fetch_balance.
        assert "BTC/USDT" in portfolio_manager.positions
        assert orchestrator._live_paper_portfolio is None


class TestExternalCapitalFlowEndToEnd:
    """Regression coverage for a real incident: moving funds into Binance's
    Predictions feature (unrelated manual account activity) dropped free
    balance by 99.6% in one cycle, and the drawdown circuit breaker
    correctly-by-its-own-logic treated that as a catastrophic trading loss
    and halted all new trading. End-to-end proof that an external flow no
    longer trips the breaker, while a genuine trading-driven drawdown
    still does."""

    def _build(self, tmp_path, free_balance, **risk_kwargs):
        from trading_assistant.agents.alerting import AlertManager

        portfolio_manager = PortfolioManager(initial_capital=10000.0)
        journal = Journal(db_path=str(tmp_path / "journal.db"))
        alert_manager = AlertManager(journal=journal)

        class LiveExchange(DummyExchange):
            def __init__(self):
                super().__init__()
                self.free_balance = free_balance

            def fetch_balance(self):
                return {"USDT": {"free": self.free_balance, "total": self.free_balance}}

        exchange = LiveExchange()
        prediction_agent = PredictionAgent(model_dir=tmp_path, auto_load=False)
        prediction_agent.set_model("BTC/USDT", DummyClassifier(up_probability=0.5), feature_columns=["close"])

        orchestrator = Orchestrator(
            market_monitor=MarketMonitorAgent(symbols=["BTC/USDT"], feeds={"BTC/USDT": FakeFeed([100.0] * 20)}),
            prediction=prediction_agent,
            risk=RiskAgent(max_position_size=0.5, max_drawdown_pct=0.30, alert_manager=alert_manager, **risk_kwargs),
            execution=ExecutionAgent(portfolio=portfolio_manager, exchange=exchange),
            portfolio=PortfolioAgent(portfolio=portfolio_manager, journal=journal),
            journal=journal,
            alert_manager=alert_manager,
            is_live=True,
            min_live_capital_usd=0.0,
        )
        return orchestrator, portfolio_manager, journal, exchange

    def test_a_manual_withdrawal_does_not_trip_the_circuit_breaker(self, tmp_path):
        orchestrator, portfolio_manager, journal, exchange = self._build(tmp_path, free_balance=2.91)
        orchestrator.run_cycle()  # establishes the baseline/peak at 2.91
        assert orchestrator.risk.circuit_breaker.tripped is False

        exchange.free_balance = 0.01  # the Predictions bet - zero trading activity
        orchestrator.run_cycle()

        assert orchestrator.risk.circuit_breaker.tripped is False
        assert portfolio_manager.capital == 0.01  # capital sync itself is unaffected - still real

        alerts = journal.recent_alerts()
        assert any(a["category"] == "external_capital_flow" for a in alerts)

    def test_a_genuine_trading_driven_drawdown_still_trips_the_circuit_breaker(self, tmp_path):
        orchestrator, portfolio_manager, journal, exchange = self._build(tmp_path, free_balance=100.0)
        orchestrator.run_cycle()  # establishes the baseline/peak at 100.0
        assert orchestrator.risk.circuit_breaker.tripped is False

        # A real, large trading loss: the balance drop is fully explained by
        # a recorded sell, not an unexplained external gap.
        portfolio_manager.open_position("BTC/USDT", price=100.0, quantity=0.5)
        orchestrator.portfolio.record_trade("BTC/USDT", "buy", price=100.0, quantity=0.5)
        portfolio_manager.close_position("BTC/USDT", price=40.0, quantity=0.5)
        orchestrator.portfolio.record_trade("BTC/USDT", "sell", price=40.0, quantity=0.5, pnl=-30.0)
        exchange.free_balance = 70.0  # 100 - 50 + 20 = 70, matches trading exactly

        orchestrator.run_cycle()

        assert orchestrator.risk.circuit_breaker.tripped is True
        assert "max drawdown breached" in orchestrator.risk.circuit_breaker.trip_reason
