import numpy as np
import pandas as pd
import pytest

from trading_assistant.agents.risk import RiskAgent, latest_atr_pct


def _empty_positions():
    return pd.DataFrame(columns=["symbol", "quantity", "price"])


def test_evaluate_buy_approves_under_normal_conditions():
    agent = RiskAgent(max_position_size=0.5)

    verdict = agent.evaluate_buy("BTC/USDT", price=100.0, capital=10000.0, positions_df=_empty_positions())

    assert verdict.approved is True
    assert verdict.quantity > 0


def test_evaluate_buy_rejects_when_no_capital():
    agent = RiskAgent()

    verdict = agent.evaluate_buy("BTC/USDT", price=100.0, capital=0.0, positions_df=_empty_positions())

    assert verdict.approved is False
    assert "capital" in verdict.reason


def test_evaluate_buy_rejects_after_daily_loss_limit_breached():
    agent = RiskAgent(max_daily_loss=0.02)
    agent.risk_manager.reset_if_new_day()
    agent.risk_manager.daily_pnl = -0.03  # already past the 2% limit

    verdict = agent.evaluate_buy("BTC/USDT", price=100.0, capital=10000.0, positions_df=_empty_positions())

    assert verdict.approved is False
    assert "daily loss" in verdict.reason


def test_evaluate_buy_rejects_when_gross_exposure_limit_already_breached():
    # capital=10000, pct=0.1 -> $1000 effective cap, same as the old
    # fixed-$1000 test this replaces.
    agent = RiskAgent(max_position_weight=None, max_gross_exposure_pct=0.1, max_net_exposure_pct=None)
    existing_positions = pd.DataFrame([{"symbol": "ETH/USDT", "quantity": 1.0, "price": 5000.0}])

    verdict = agent.evaluate_buy("BTC/USDT", price=100.0, capital=10000.0, positions_df=existing_positions)

    assert verdict.approved is False
    assert "max_gross_exposure" in verdict.reason


def test_evaluate_buy_rejects_on_first_trade_when_proposed_size_alone_breaches_gross_exposure():
    """Regression test: the exposure check used to be skipped entirely when
    there were no existing positions, so a first trade could never be
    rejected on exposure grounds no matter how large."""
    # capital=10000, pct=0.05 -> $500 effective cap, same as the old
    # fixed-$500 test this replaces.
    agent = RiskAgent(
        max_position_size=0.9, max_position_weight=None, max_gross_exposure_pct=0.05, max_net_exposure_pct=None
    )

    verdict = agent.evaluate_buy("BTC/USDT", price=100.0, capital=10000.0, positions_df=_empty_positions())

    assert verdict.approved is False
    assert "max_gross_exposure" in verdict.reason


def test_gross_exposure_cap_tracks_live_capital_not_a_fixed_dollar_amount():
    """Regression: max_gross_exposure/max_net_exposure used to be fixed
    dollar caps tied to whatever capital happened to be true when the
    config was written - silently wrong the moment real capital diverged
    (a deposit made the cap too loose to matter; a withdrawal made it too
    tight). A ratio of live capital must scale automatically with no
    config change needed."""
    # max_position_size capped low so the CANDIDATE trade alone stays well
    # under the exposure cap, leaving headroom to isolate what this test is
    # actually about: whether the $50 existing position measured against
    # that cap moves with capital.
    agent = RiskAgent(
        max_position_size=0.05, max_position_weight=None, max_gross_exposure_pct=0.1, max_net_exposure_pct=None
    )
    existing_positions = pd.DataFrame([{"symbol": "ETH/USDT", "quantity": 1.0, "price": 50.0}])

    # $50 held plus a <=$500 candidate (5% of $10,000), 10% of $10,000
    # capital = $1000 cap - comfortably clear.
    small_capital_verdict = agent.evaluate_buy(
        "BTC/USDT", price=1.0, capital=10000.0, positions_df=existing_positions
    )
    # The SAME $50 held is trivially over a 10% cap of a much smaller
    # $100 capital ($10 cap) - the identical portfolio must now be
    # rejected, proving the effective cap moved with capital.
    large_relative_exposure_verdict = agent.evaluate_buy(
        "BTC/USDT", price=1.0, capital=100.0, positions_df=existing_positions
    )

    assert small_capital_verdict.approved is True
    assert large_relative_exposure_verdict.approved is False
    assert "max_gross_exposure" in large_relative_exposure_verdict.reason


def test_evaluate_sell_always_approved():
    agent = RiskAgent()

    verdict = agent.evaluate_sell("BTC/USDT")

    assert verdict.approved is True


def test_record_realized_pnl_feeds_daily_loss_counter():
    agent = RiskAgent(max_daily_loss=0.02)

    agent.record_realized_pnl(pnl=-150.0, capital=10000.0)  # -1.5%

    assert agent.risk_manager.daily_pnl == pytest.approx(-0.015)
    assert agent.risk_manager.check_daily_loss_limit() is True


def test_evaluate_buy_rejects_when_circuit_breaker_already_tripped():
    agent = RiskAgent()
    agent.circuit_breaker.trip("3 consecutive execution errors")

    verdict = agent.evaluate_buy("BTC/USDT", price=100.0, capital=10000.0, positions_df=_empty_positions())

    assert verdict.approved is False
    assert "circuit breaker" in verdict.reason


def test_daily_loss_breach_trips_the_circuit_breaker():
    agent = RiskAgent(max_daily_loss=0.02)
    agent.risk_manager.reset_if_new_day()
    agent.risk_manager.daily_pnl = -0.03

    agent.evaluate_buy("BTC/USDT", price=100.0, capital=10000.0, positions_df=_empty_positions())

    assert agent.circuit_breaker.tripped is True
    assert "daily loss" in agent.circuit_breaker.trip_reason

    # even after the day rolls over and daily_pnl resets, the breaker stays tripped
    from datetime import date, timedelta

    agent.risk_manager.reset_if_new_day(date.today() + timedelta(days=1))
    verdict = agent.evaluate_buy("BTC/USDT", price=100.0, capital=10000.0, positions_df=_empty_positions())
    assert verdict.approved is False
    assert "circuit breaker" in verdict.reason


def test_evaluate_sell_is_never_blocked_by_circuit_breaker():
    agent = RiskAgent()
    agent.circuit_breaker.trip("some serious failure")

    verdict = agent.evaluate_sell("BTC/USDT")

    assert verdict.approved is True


def _lockstep_history(n=30):
    base = pd.Series([100.0 + i for i in range(n)])
    return {"BTC/USDT": base, "ETH/USDT": base * 20}  # perfectly correlated (same relative moves)


def test_evaluate_buy_rejects_when_correlated_cluster_exposure_too_high():
    """Per-symbol caps alone can't see that BTC/USDT and ETH/USDT moving in
    lockstep amount to one concentrated bet, not two independent ones."""
    agent = RiskAgent(
        max_position_size=0.5,
        max_gross_exposure_pct=None,
        max_net_exposure_pct=None,
        max_position_weight=None,
        max_correlated_exposure_pct=0.3,
        correlation_threshold=0.6,
    )
    held = pd.DataFrame([{"symbol": "BTC/USDT", "quantity": 20.0, "price": 100.0}])  # $2000 held

    verdict = agent.evaluate_buy(
        "ETH/USDT", price=2000.0, capital=8000.0, positions_df=held, price_history=_lockstep_history()
    )

    assert verdict.approved is False
    assert "correlated-cluster exposure" in verdict.reason


def test_evaluate_buy_approves_when_correlated_cluster_exposure_within_cap():
    agent = RiskAgent(
        max_position_size=0.05,  # small trade size keeps combined exposure under the cap
        max_gross_exposure_pct=None,
        max_net_exposure_pct=None,
        max_position_weight=None,
        max_correlated_exposure_pct=0.9,
        correlation_threshold=0.6,
    )
    held = pd.DataFrame([{"symbol": "BTC/USDT", "quantity": 1.0, "price": 100.0}])  # $100 held

    verdict = agent.evaluate_buy(
        "ETH/USDT", price=2000.0, capital=9900.0, positions_df=held, price_history=_lockstep_history()
    )

    assert verdict.approved is True


def test_evaluate_buy_skips_correlation_check_when_not_configured():
    """Backward compatible: existing callers that never opt in (no
    max_correlated_exposure_pct, no price_history) keep the original
    behavior, even with a heavily-correlated existing book."""
    agent = RiskAgent(max_position_size=0.5, max_gross_exposure_pct=None, max_net_exposure_pct=None, max_position_weight=None)
    held = pd.DataFrame([{"symbol": "BTC/USDT", "quantity": 100.0, "price": 100.0}])

    verdict = agent.evaluate_buy("ETH/USDT", price=2000.0, capital=1000.0, positions_df=held)

    assert verdict.approved is True


def test_evaluate_buy_ignores_uncorrelated_held_positions_for_the_cluster_cap():
    agent = RiskAgent(
        max_position_size=0.5,
        max_gross_exposure_pct=None,
        max_net_exposure_pct=None,
        max_position_weight=None,
        max_correlated_exposure_pct=0.15,
        correlation_threshold=0.9,  # high bar an independent random walk won't clear
    )
    import numpy as np

    rng = np.random.default_rng(7)
    unrelated = pd.Series(50 + np.cumsum(rng.normal(0, 1, 30)))
    candidate = pd.Series(100 + np.cumsum(rng.normal(0, 1, 30) * -1))
    held = pd.DataFrame([{"symbol": "OLD/USDT", "quantity": 100.0, "price": 90.0}])  # $9000 held, uncorrelated

    verdict = agent.evaluate_buy(
        "NEW/USDT",
        price=100.0,
        capital=1000.0,
        positions_df=held,
        price_history={"OLD/USDT": unrelated, "NEW/USDT": candidate},
    )

    assert verdict.approved is True  # OLD/USDT's $9000 must not count toward NEW/USDT's cluster


class TestCrisisCorrelationOverride:
    def test_treats_an_uncorrelated_position_as_correlated_while_crisis_is_active(self):
        """The core behavior: a position that would normally pass the
        correlation gate (measured correlation below threshold) must be
        treated as fully correlated once the crisis override is active -
        that's the entire point of the override."""
        agent = RiskAgent(
            max_position_size=0.5, max_gross_exposure_pct=None, max_net_exposure_pct=None, max_position_weight=None,
            max_correlated_exposure_pct=0.15, correlation_threshold=0.9,
            crisis_drop_threshold=0.15,
        )
        import numpy as np
        from datetime import datetime, timezone

        rng = np.random.default_rng(7)
        unrelated = pd.Series(50 + np.cumsum(rng.normal(0, 1, 30)))
        candidate = pd.Series(100 + np.cumsum(rng.normal(0, 1, 30) * -1))
        held = pd.DataFrame([{"symbol": "OLD/USDT", "quantity": 100.0, "price": 90.0}])  # $9000 held, uncorrelated
        # simulate an already-triggered crisis directly (isolates this test
        # from update_crisis_state's own OHLCV-parsing behavior, covered separately)
        agent._crisis_state.triggered_at = datetime.now(timezone.utc)
        agent._crisis_state.reference_price = 100000.0

        verdict = agent.evaluate_buy(
            "NEW/USDT", price=100.0, capital=1000.0, positions_df=held,
            price_history={"OLD/USDT": unrelated, "NEW/USDT": candidate, "BTC/USDT": pd.Series([80000.0])},
        )

        assert verdict.approved is False
        assert "crisis correlation override active" in verdict.reason

    def test_disabled_by_default_even_with_an_active_crisis_state(self):
        """Backward compatible: crisis_drop_threshold=None (the default)
        means the override never engages, regardless of _crisis_state."""
        agent = RiskAgent(
            max_position_size=0.5, max_gross_exposure_pct=None, max_net_exposure_pct=None, max_position_weight=None,
            max_correlated_exposure_pct=0.9, correlation_threshold=0.9,
        )
        held = pd.DataFrame([{"symbol": "BTC/USDT", "quantity": 1.0, "price": 100.0}])

        verdict = agent.evaluate_buy(
            "ETH/USDT", price=2000.0, capital=9900.0, positions_df=held, price_history=_lockstep_history(),
        )

        assert verdict.approved is True

    def test_update_crisis_state_triggers_from_a_real_ohlcv_frame(self):
        """End-to-end: update_crisis_state (fed the crisis symbol's OHLCV
        frame, as the orchestrator would every cycle) actually triggers
        the override that evaluate_buy then enforces."""
        agent = RiskAgent(
            max_position_size=0.5, max_gross_exposure_pct=None, max_net_exposure_pct=None, max_position_weight=None,
            max_correlated_exposure_pct=0.15, correlation_threshold=0.9,
            crisis_drop_threshold=0.15, crisis_window_hours=1.0,
        )
        btc_frame = pd.DataFrame({
            "timestamp": pd.date_range("2026-01-01T00:00:00Z", periods=7, freq="10min", tz="UTC"),
            "close": [100000.0, 98000.0, 95000.0, 90000.0, 85000.0, 82000.0, 80000.0],  # 20% drop in 1h
        })

        agent.update_crisis_state(btc_frame)

        import numpy as np
        rng = np.random.default_rng(7)
        unrelated = pd.Series(50 + np.cumsum(rng.normal(0, 1, 30)))
        candidate = pd.Series(100 + np.cumsum(rng.normal(0, 1, 30) * -1))
        held = pd.DataFrame([{"symbol": "OLD/USDT", "quantity": 100.0, "price": 90.0}])

        verdict = agent.evaluate_buy(
            "NEW/USDT", price=100.0, capital=1000.0, positions_df=held,
            price_history={"OLD/USDT": unrelated, "NEW/USDT": candidate, "BTC/USDT": pd.Series([80000.0])},
        )

        assert verdict.approved is False
        assert "crisis correlation override active" in verdict.reason

    def test_update_crisis_state_is_a_no_op_when_not_configured(self):
        agent = RiskAgent()  # crisis_drop_threshold defaults to None
        btc_frame = pd.DataFrame({
            "timestamp": pd.date_range("2026-01-01T00:00:00Z", periods=7, freq="10min", tz="UTC"),
            "close": [100000.0, 98000.0, 95000.0, 90000.0, 85000.0, 82000.0, 80000.0],
        })

        agent.update_crisis_state(btc_frame)  # must not raise, must not set state

        assert agent._crisis_state.triggered_at is None


from trading_assistant.risk.adaptive import DrawdownTier

CUSTOM_TIERS = [DrawdownTier(0.05, 0.75), DrawdownTier(0.10, 0.5), DrawdownTier(0.20, 0.0)]


def test_update_equity_tracks_peak_and_scales_down_position_size_during_drawdown():
    agent = RiskAgent(risk_per_trade=0.02, stop_loss_pct=0.01, drawdown_tiers=CUSTOM_TIERS)
    agent.update_equity(10000.0)  # establishes the peak
    agent.update_equity(9400.0)  # 6% drawdown -> 0.75 scale tier

    verdict = agent.evaluate_buy("BTC/USDT", price=100.0, capital=9400.0, positions_df=_empty_positions())

    baseline = RiskAgent(risk_per_trade=0.02, stop_loss_pct=0.01)
    baseline_verdict = baseline.evaluate_buy("BTC/USDT", price=100.0, capital=9400.0, positions_df=_empty_positions())
    assert verdict.approved is True
    assert verdict.quantity == pytest.approx(baseline_verdict.quantity * 0.75)


def test_update_equity_does_not_retreat_the_peak_on_a_pullback():
    agent = RiskAgent(drawdown_tiers=CUSTOM_TIERS)
    agent.update_equity(10000.0)
    agent.update_equity(9000.0)  # pull back
    agent.update_equity(9500.0)  # partial recovery, still below the original peak

    assert agent._peak_equity == 10000.0  # the peak must not have been overwritten by 9500


def test_deep_drawdown_halts_new_positions_entirely():
    agent = RiskAgent(drawdown_tiers=CUSTOM_TIERS)
    agent.update_equity(10000.0)
    agent.update_equity(7500.0)  # 25% drawdown - past the deepest (20%) tier

    verdict = agent.evaluate_buy("BTC/USDT", price=100.0, capital=7500.0, positions_df=_empty_positions())

    assert verdict.approved is False
    assert "drawdown circuit halt" in verdict.reason


def test_evaluate_sell_still_approved_during_a_drawdown_halt():
    """Flattening a position always reduces risk - a drawdown halt on NEW
    positions must never block exiting an existing one."""
    agent = RiskAgent(drawdown_tiers=CUSTOM_TIERS)
    agent.update_equity(10000.0)
    agent.update_equity(7500.0)

    assert agent.evaluate_sell("BTC/USDT").approved is True


def test_drawdown_scaling_disabled_by_default_even_if_equity_drops():
    """Backward compatible: existing callers that never opt in
    (drawdown_tiers=None, the default) keep full position sizing regardless
    of any equity drop, since update_equity is a no-op for scaling purposes."""
    agent = RiskAgent(risk_per_trade=0.02, stop_loss_pct=0.01)  # drawdown_tiers defaults to None
    agent.update_equity(10000.0)
    agent.update_equity(5000.0)  # a severe 50% drop

    verdict = agent.evaluate_buy("BTC/USDT", price=100.0, capital=5000.0, positions_df=_empty_positions())

    baseline = RiskAgent(risk_per_trade=0.02, stop_loss_pct=0.01)
    baseline_verdict = baseline.evaluate_buy("BTC/USDT", price=100.0, capital=5000.0, positions_df=_empty_positions())
    assert verdict.quantity == pytest.approx(baseline_verdict.quantity)  # unaffected


def test_update_equity_ignores_non_positive_values():
    agent = RiskAgent(drawdown_tiers=CUSTOM_TIERS)
    agent.update_equity(10000.0)
    agent.update_equity(0.0)
    agent.update_equity(-500.0)

    assert agent._peak_equity == 10000.0  # neither bogus value touched the peak


class TestMaxDrawdownCircuitBreakerTrip:
    """Distinct from drawdown_tiers' soft auto-scaling (which floors new-
    position risk to zero at its deepest tier but auto-recovers silently
    as equity recovers): max_drawdown_pct is a HARD stop that trips the
    circuit breaker, requiring a deliberate operator reset - the same
    "someone must look at this" bar max_daily_loss already enforces."""

    def test_trips_the_circuit_breaker_once_drawdown_crosses_the_threshold(self):
        agent = RiskAgent(max_drawdown_pct=0.25)
        agent.update_equity(10000.0)

        agent.update_equity(7400.0)  # 26% drawdown - past the 25% threshold

        assert agent.circuit_breaker.tripped is True
        assert "max drawdown breached" in agent.circuit_breaker.trip_reason

    def test_does_not_trip_below_the_threshold(self):
        agent = RiskAgent(max_drawdown_pct=0.25)
        agent.update_equity(10000.0)

        agent.update_equity(8000.0)  # 20% drawdown - below the 25% threshold

        assert agent.circuit_breaker.tripped is False

    def test_disabled_by_default(self):
        agent = RiskAgent()  # max_drawdown_pct defaults to None
        agent.update_equity(10000.0)

        agent.update_equity(1000.0)  # a catastrophic 90% drawdown

        assert agent.circuit_breaker.tripped is False

    def test_a_trip_here_also_blocks_new_buys_via_the_shared_circuit_breaker(self):
        agent = RiskAgent(max_drawdown_pct=0.25)
        agent.update_equity(10000.0)
        agent.update_equity(7000.0)  # 30% drawdown

        verdict = agent.evaluate_buy("BTC/USDT", price=100.0, capital=7000.0, positions_df=_empty_positions())

        assert verdict.approved is False
        assert "circuit breaker tripped" in verdict.reason

    def test_combines_correctly_with_drawdown_tiers_soft_scaling(self):
        """Both mechanisms can run together: tiers scale size down as
        equity falls, and the hard threshold trips the breaker once it
        falls far enough - they are not mutually exclusive."""
        agent = RiskAgent(drawdown_tiers=CUSTOM_TIERS, max_drawdown_pct=0.30)
        agent.update_equity(10000.0)
        agent.update_equity(9400.0)  # 6% - within tiers, no trip

        assert agent.circuit_breaker.tripped is False
        assert agent._current_risk_scale == 0.75

        agent.update_equity(6500.0)  # 35% - past the hard threshold

        assert agent.circuit_breaker.tripped is True


class TestAdjustPeakEquityForExternalFlow:
    """Regression coverage for a real incident: moving funds into Binance's
    Predictions feature (a manual, non-trading account action) dropped free
    balance by 99.6%, and the drawdown circuit breaker correctly-by-its-own-
    logic treated that as a catastrophic loss. This is the other half of the
    fix (see PortfolioAgent.sync_capital_with_exchange / cash_flows.py for
    the detection side) - rebasing the peak so a detected external flow
    never registers as trading performance."""

    def test_a_withdrawal_rebases_the_peak_so_it_does_not_look_like_a_drawdown(self):
        agent = RiskAgent(max_drawdown_pct=0.30)
        agent.update_equity(2.91)

        agent.adjust_peak_equity_for_external_flow(-2.90)  # the Predictions bet
        agent.update_equity(0.01)  # the real, now-lower balance

        assert agent.circuit_breaker.tripped is False

    def test_a_deposit_rebases_the_peak_up_so_it_is_not_read_as_a_windfall(self):
        agent = RiskAgent(max_drawdown_pct=0.30)
        agent.update_equity(100.0)

        agent.adjust_peak_equity_for_external_flow(400.0)  # a deposit
        agent.update_equity(500.0)

        assert agent._peak_equity == pytest.approx(500.0)
        assert agent.circuit_breaker.tripped is False

    def test_a_real_trading_loss_after_a_withdrawal_still_trips_correctly(self):
        # The withdrawal itself must not trip anything, but real trading
        # losses afterward - measured against the REBASED peak - still must.
        agent = RiskAgent(max_drawdown_pct=0.30)
        agent.update_equity(100.0)

        agent.adjust_peak_equity_for_external_flow(-50.0)  # withdrew half
        agent.update_equity(50.0)
        assert agent.circuit_breaker.tripped is False

        agent.update_equity(30.0)  # a genuine 40% loss on what's left

        assert agent.circuit_breaker.tripped is True

    def test_is_a_no_op_before_any_peak_has_been_established(self):
        agent = RiskAgent(max_drawdown_pct=0.30)

        agent.adjust_peak_equity_for_external_flow(-100.0)  # must not raise

        assert agent._peak_equity is None


def _trending_ohlcv(n=200, start=100.0, step=1.0):
    closes = [start + i * step for i in range(n)]
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC"),
            "open": closes,
            "high": [c + 1 for c in closes],
            "low": [c - 1 for c in closes],
            "close": closes,
            "volume": [100.0] * n,
        }
    )


def test_evaluate_buy_rejects_against_a_confirmed_higher_timeframe_downtrend():
    agent = RiskAgent(max_position_size=0.5, higher_timeframe_factor=4)
    declining = _trending_ohlcv(n=200, start=300.0, step=-1.0)

    verdict = agent.evaluate_buy(
        "BTC/USDT", price=100.0, capital=10000.0, positions_df=_empty_positions(), ohlcv_frame=declining
    )

    assert verdict.approved is False
    assert "higher-timeframe confluence" in verdict.reason


def test_evaluate_buy_approves_with_a_confirmed_higher_timeframe_uptrend():
    agent = RiskAgent(max_position_size=0.5, higher_timeframe_factor=4)
    rising = _trending_ohlcv(n=200, start=100.0, step=1.0)

    verdict = agent.evaluate_buy(
        "BTC/USDT", price=100.0, capital=10000.0, positions_df=_empty_positions(), ohlcv_frame=rising
    )

    assert verdict.approved is True


def test_evaluate_buy_approves_when_higher_timeframe_history_is_insufficient():
    """Not enough resampled higher-timeframe history to classify a trend is
    a data gap, not a veto - it must never block a trade the same way a
    genuinely confirmed downtrend would."""
    agent = RiskAgent(max_position_size=0.5, higher_timeframe_factor=4)
    short_history = _trending_ohlcv(n=50, start=300.0, step=-1.0)  # too short to resample meaningfully

    verdict = agent.evaluate_buy(
        "BTC/USDT", price=100.0, capital=10000.0, positions_df=_empty_positions(), ohlcv_frame=short_history
    )

    assert verdict.approved is True


def test_evaluate_buy_skips_confluence_check_when_not_configured():
    """Backward compatible: existing callers that never opt in (no
    higher_timeframe_factor, no ohlcv_frame) keep the original behavior,
    even against a heavily-declining price history."""
    agent = RiskAgent(max_position_size=0.5)  # higher_timeframe_factor defaults to None
    declining = _trending_ohlcv(n=200, start=300.0, step=-1.0)

    verdict = agent.evaluate_buy(
        "BTC/USDT", price=100.0, capital=10000.0, positions_df=_empty_positions(), ohlcv_frame=declining
    )

    assert verdict.approved is True


def test_evaluate_buy_rejects_when_price_has_fallen_below_the_fast_sma():
    agent = RiskAgent(max_position_size=0.5, trend_alignment_fast_period=20, trend_alignment_slow_period=50)
    # A steady uptrend (so SMA20 sits above SMA50, the normal aligned
    # ordering), then a sudden one-bar drop that dips price below SMA20
    # without falling below the lower SMA50.
    closes = list(np.linspace(80.0, 100.0, 49)) + [90.0]
    frame = _trending_ohlcv(n=len(closes))
    frame["close"] = closes
    frame["open"] = closes
    frame["high"] = [c + 1 for c in closes]
    frame["low"] = [c - 1 for c in closes]

    verdict = agent.evaluate_buy(
        "BTC/USDT", price=100.0, capital=10000.0, positions_df=_empty_positions(), ohlcv_frame=frame
    )

    assert verdict.approved is False
    assert "trend alignment" in verdict.reason


def test_evaluate_buy_approves_when_price_is_fully_aligned_above_both_smas():
    agent = RiskAgent(max_position_size=0.5, trend_alignment_fast_period=20, trend_alignment_slow_period=50)
    rising = _trending_ohlcv(n=100, start=100.0, step=1.0)

    verdict = agent.evaluate_buy(
        "BTC/USDT", price=100.0, capital=10000.0, positions_df=_empty_positions(), ohlcv_frame=rising
    )

    assert verdict.approved is True


def test_evaluate_buy_approves_when_trend_alignment_history_is_insufficient():
    """Not enough history to compute both SMAs is a data gap, not a veto."""
    agent = RiskAgent(max_position_size=0.5, trend_alignment_fast_period=20, trend_alignment_slow_period=50)
    short_history = _trending_ohlcv(n=10, start=100.0, step=-1.0)

    verdict = agent.evaluate_buy(
        "BTC/USDT", price=100.0, capital=10000.0, positions_df=_empty_positions(), ohlcv_frame=short_history
    )

    assert verdict.approved is True


def test_evaluate_buy_skips_trend_alignment_check_when_not_configured():
    """Backward compatible: existing callers that never opt in (no
    trend_alignment_fast_period) keep the original behavior, even against
    a heavily-declining price history."""
    agent = RiskAgent(max_position_size=0.5)  # trend_alignment_fast_period defaults to None
    declining = _trending_ohlcv(n=200, start=300.0, step=-1.0)

    verdict = agent.evaluate_buy(
        "BTC/USDT", price=100.0, capital=10000.0, positions_df=_empty_positions(), ohlcv_frame=declining
    )

    assert verdict.approved is True


def test_evaluate_buy_rejects_when_sma_slope_is_not_rising():
    """A climb, then a decline, then a partial bounce: price > SMA20 >
    SMA50 can be satisfied by the bounce alone while SMA20's own trailing
    window is still dominated by the preceding decline - "aligned" alone
    doesn't mean the average itself has turned back upward yet."""
    agent = RiskAgent(
        max_position_size=0.5, trend_alignment_fast_period=20, trend_alignment_slow_period=50,
        require_sma_slope_rising=True,
    )
    climb = list(np.linspace(80.0, 110.0, 40))
    decline = list(np.linspace(110.0, 100.0, 20))
    bounce = [106.0, 108.0]
    closes = climb + decline + bounce
    frame = _trending_ohlcv(n=len(closes))
    frame["close"] = closes
    frame["open"] = closes
    frame["high"] = [c + 1 for c in closes]
    frame["low"] = [c - 1 for c in closes]

    verdict = agent.evaluate_buy(
        "BTC/USDT", price=108.0, capital=10000.0, positions_df=_empty_positions(), ohlcv_frame=frame
    )

    assert verdict.approved is False
    assert "sloping upward" in verdict.reason


def test_evaluate_buy_approves_with_slope_check_on_a_genuine_uptrend():
    agent = RiskAgent(
        max_position_size=0.5, trend_alignment_fast_period=20, trend_alignment_slow_period=50,
        require_sma_slope_rising=True,
    )
    rising = _trending_ohlcv(n=100, start=100.0, step=1.0)

    verdict = agent.evaluate_buy(
        "BTC/USDT", price=100.0, capital=10000.0, positions_df=_empty_positions(), ohlcv_frame=rising
    )

    assert verdict.approved is True


def test_evaluate_buy_rejects_below_adx_threshold_on_a_choppy_market():
    agent = RiskAgent(max_position_size=0.5, adx_threshold=25.0)
    # An oscillating, directionless series - low ADX (no sustained trend).
    n = 100
    closes = [100.0 + (5.0 if i % 2 == 0 else -5.0) for i in range(n)]
    frame = _trending_ohlcv(n=n)
    frame["close"] = closes
    frame["open"] = closes
    frame["high"] = [c + 1 for c in closes]
    frame["low"] = [c - 1 for c in closes]

    verdict = agent.evaluate_buy(
        "BTC/USDT", price=100.0, capital=10000.0, positions_df=_empty_positions(), ohlcv_frame=frame
    )

    assert verdict.approved is False
    assert "ADX confirmation" in verdict.reason


def test_evaluate_buy_approves_above_adx_threshold_on_a_strong_trend():
    agent = RiskAgent(max_position_size=0.5, adx_threshold=25.0)
    strong_trend = _trending_ohlcv(n=100, start=50.0, step=2.0)

    verdict = agent.evaluate_buy(
        "BTC/USDT", price=100.0, capital=10000.0, positions_df=_empty_positions(), ohlcv_frame=strong_trend
    )

    assert verdict.approved is True


def test_evaluate_buy_rejects_below_volume_confirmation_threshold():
    agent = RiskAgent(max_position_size=0.5, volume_confirmation_threshold=1.0)
    frame = _trending_ohlcv(n=100, start=100.0, step=1.0)
    frame["volume"] = [100.0] * 99 + [10.0]  # last bar's volume far below its own rolling average

    verdict = agent.evaluate_buy(
        "BTC/USDT", price=100.0, capital=10000.0, positions_df=_empty_positions(), ohlcv_frame=frame
    )

    assert verdict.approved is False
    assert "volume confirmation" in verdict.reason


def test_evaluate_buy_approves_above_volume_confirmation_threshold():
    agent = RiskAgent(max_position_size=0.5, volume_confirmation_threshold=1.0)
    frame = _trending_ohlcv(n=100, start=100.0, step=1.0)
    frame["volume"] = [100.0] * 100  # flat volume, exactly at its own average

    verdict = agent.evaluate_buy(
        "BTC/USDT", price=100.0, capital=10000.0, positions_df=_empty_positions(), ohlcv_frame=frame
    )

    assert verdict.approved is True


def test_evaluate_buy_skips_new_confluence_checks_when_not_configured():
    """Backward compatible: none of the three new checks (slope/ADX/volume)
    fire unless explicitly opted into."""
    agent = RiskAgent(max_position_size=0.5)
    choppy = _trending_ohlcv(n=100)
    choppy["close"] = [100.0 + (5.0 if i % 2 == 0 else -5.0) for i in range(100)]
    choppy["volume"] = [100.0] * 99 + [1.0]

    verdict = agent.evaluate_buy(
        "BTC/USDT", price=100.0, capital=10000.0, positions_df=_empty_positions(), ohlcv_frame=choppy
    )

    assert verdict.approved is True


def test_circuit_breaker_tracks_consecutive_errors_per_symbol():
    """Regression test: a shared counter would let one symbol's success
    reset another symbol's failures, so a permanently broken symbol could
    never trip the breaker on its own."""
    agent = RiskAgent(max_consecutive_errors=3)

    agent.circuit_breaker.record_result("BTC/USDT", success=False)
    agent.circuit_breaker.record_result("BTC/USDT", success=False)
    agent.circuit_breaker.record_result("ETH/USDT", success=True)  # must not reset BTC/USDT's count
    agent.circuit_breaker.record_result("BTC/USDT", success=False)

    assert agent.circuit_breaker.tripped is True
    assert "BTC/USDT" in agent.circuit_breaker.trip_reason


class TestVolatilityAdjustedSizing:
    def test_disabled_by_default_uses_the_static_stop_loss_pct(self):
        agent = RiskAgent(max_position_size=0.5, stop_loss_pct=0.02)  # atr_multiplier defaults to None

        verdict = agent.evaluate_buy(
            "BTC/USDT", price=100.0, capital=10000.0, positions_df=_empty_positions(), ohlcv_frame=_trending_ohlcv()
        )

        assert verdict.stop_loss_pct == 0.02

    def test_enabled_returns_a_volatility_derived_stop(self):
        agent = RiskAgent(max_position_size=0.5, stop_loss_pct=0.01, atr_multiplier=2.0)

        verdict = agent.evaluate_buy(
            "BTC/USDT", price=200.0, capital=10000.0, positions_df=_empty_positions(), ohlcv_frame=_trending_ohlcv()
        )

        assert verdict.stop_loss_pct is not None
        assert verdict.stop_loss_pct != 0.01  # actually derived from ATR, not the static fallback

    def test_falls_back_to_static_stop_without_an_ohlcv_frame(self):
        agent = RiskAgent(max_position_size=0.5, stop_loss_pct=0.015, atr_multiplier=2.0)

        verdict = agent.evaluate_buy("BTC/USDT", price=100.0, capital=10000.0, positions_df=_empty_positions())

        assert verdict.stop_loss_pct == 0.015

    def test_wider_stop_sizes_a_smaller_position_for_the_same_dollar_risk(self):
        """The whole point: a volatility-widened stop must shrink position
        size, since size = risk_amount / stop_distance."""
        tight = RiskAgent(max_position_size=1.0, risk_per_trade=0.02, stop_loss_pct=0.01)
        wide = RiskAgent(max_position_size=1.0, risk_per_trade=0.02, stop_loss_pct=0.05)

        tight_verdict = tight.evaluate_buy("BTC/USDT", price=100.0, capital=10000.0, positions_df=_empty_positions())
        wide_verdict = wide.evaluate_buy("BTC/USDT", price=100.0, capital=10000.0, positions_df=_empty_positions())

        assert wide_verdict.quantity < tight_verdict.quantity

    def test_clamped_within_configured_bounds(self):
        # a flat/near-zero-range series -> near-zero ATR -> must clamp to min_atr_stop_pct, not collapse to ~0
        flat = pd.DataFrame(
            {
                "timestamp": pd.date_range("2026-01-01", periods=60, freq="h", tz="UTC"),
                "open": [100.0] * 60,
                "high": [100.0001] * 60,
                "low": [99.9999] * 60,
                "close": [100.0] * 60,
                "volume": [100.0] * 60,
            }
        )
        agent = RiskAgent(max_position_size=0.5, atr_multiplier=2.0, min_atr_stop_pct=0.005, max_atr_stop_pct=0.05)

        verdict = agent.evaluate_buy(
            "BTC/USDT", price=100.0, capital=10000.0, positions_df=_empty_positions(), ohlcv_frame=flat
        )

        assert verdict.stop_loss_pct == 0.005


class TestLatestAtrPct:
    def _ohlcv(self, n=60):
        import numpy as np

        rng = np.random.default_rng(3)
        closes = 100 + np.cumsum(rng.normal(0, 0.5, n))
        return pd.DataFrame(
            {
                "timestamp": pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC"),
                "open": closes,
                "high": closes + 1,
                "low": closes - 1,
                "close": closes,
                "volume": [100.0] * n,
            }
        )

    def test_extracts_a_real_value_once_theres_enough_history(self):
        value = latest_atr_pct(self._ohlcv())
        assert value is not None
        assert value > 0

    def test_none_for_empty_frame(self):
        assert latest_atr_pct(pd.DataFrame()) is None
        assert latest_atr_pct(None) is None

    def test_none_when_not_enough_warmup_history(self):
        assert latest_atr_pct(self._ohlcv(n=3)) is None


class TestKellyPositionSizing:
    # A generous max_daily_loss keeps these tests isolated to Kelly sizing
    # specifically - record_realized_pnl also feeds the (unrelated)
    # daily-loss circuit breaker, which would otherwise trip first on a
    # deliberately-bad synthetic trade sequence and mask what's actually
    # under test here.
    def _feed_trades(self, agent, fractions, capital=10000.0):
        for f in fractions:
            agent.record_realized_pnl(f * capital, capital)

    def test_disabled_by_default(self):
        agent = RiskAgent(max_position_size=1.0, max_daily_loss=10.0)  # kelly_min_trades defaults to None
        self._feed_trades(agent, [-0.05] * 20)  # a terrible track record

        verdict = agent.evaluate_buy("BTC/USDT", price=100.0, capital=10000.0, positions_df=_empty_positions())

        assert verdict.approved is True  # never scaled - feature is off

    def test_no_cap_before_the_minimum_sample_size_is_reached(self):
        agent = RiskAgent(max_position_size=1.0, max_daily_loss=10.0, kelly_min_trades=10)
        self._feed_trades(agent, [-0.05] * 5)  # only 5 trades, needs 10

        verdict = agent.evaluate_buy("BTC/USDT", price=100.0, capital=10000.0, positions_df=_empty_positions())

        assert verdict.approved is True

    def test_caps_size_after_a_genuinely_poor_track_record(self):
        agent = RiskAgent(max_position_size=1.0, max_daily_loss=10.0, risk_per_trade=0.5, kelly_min_trades=10)
        # far more losses than wins, losses bigger than wins - no statistical edge
        self._feed_trades(agent, [-0.05] * 8 + [0.01] * 2)

        verdict = agent.evaluate_buy("BTC/USDT", price=100.0, capital=10000.0, positions_df=_empty_positions())

        assert verdict.approved is False
        assert "zero" in verdict.reason

    def test_does_not_shrink_size_when_track_record_is_strong(self):
        agent = RiskAgent(
            max_position_size=1.0, max_daily_loss=10.0, risk_per_trade=0.01, stop_loss_pct=0.5, kelly_min_trades=10
        )
        baseline = RiskAgent(max_position_size=1.0, max_daily_loss=10.0, risk_per_trade=0.01, stop_loss_pct=0.5)
        # strong, consistent track record - Kelly should not need to intervene
        self._feed_trades(agent, [0.05] * 8 + [-0.01] * 2)

        verdict = agent.evaluate_buy("BTC/USDT", price=100.0, capital=10000.0, positions_df=_empty_positions())
        baseline_verdict = baseline.evaluate_buy(
            "BTC/USDT", price=100.0, capital=10000.0, positions_df=_empty_positions()
        )

        assert verdict.quantity == pytest.approx(baseline_verdict.quantity)

    def test_all_winning_trades_does_not_crash_or_cap(self):
        agent = RiskAgent(max_position_size=1.0, max_daily_loss=10.0, kelly_min_trades=5)
        self._feed_trades(agent, [0.05] * 10)  # no losses at all - ratio is undefined

        verdict = agent.evaluate_buy("BTC/USDT", price=100.0, capital=10000.0, positions_df=_empty_positions())

        assert verdict.approved is True

    def test_kelly_scale_shrinks_toward_zero_with_a_small_marginal_sample(self):
        """Regression: _kelly_position_scale used to size off the raw
        win_rate point estimate - right at kelly_min_trades, a marginal
        (not obviously strong) track record got exactly as much confidence
        as a much larger sample would. Now uses wilson_lower_bound, a
        conservative shrinkage estimate that's meaningfully below the raw
        point estimate for a small sample."""
        agent = RiskAgent(max_position_size=1.0, max_daily_loss=10.0, kelly_min_trades=10)
        # 60% win rate, 1:1 payoff - a real but marginal, small-sample edge
        self._feed_trades(agent, [0.02] * 6 + [-0.02] * 4)

        scale = agent._kelly_position_scale()
        raw_point_estimate_kelly = agent.risk_manager.calculate_kelly_fraction(0.6, 0.02, -0.02)

        assert scale < raw_point_estimate_kelly

    def test_kelly_scale_moves_closer_to_the_raw_estimate_with_a_larger_sample(self):
        """Same 60%/1:1 track record throughout - a larger sample's
        confidence interval is narrower, so its shrunk Kelly scale should
        sit closer to the raw point-estimate Kelly than a small sample's
        does, even if neither has fully converged at these sample sizes
        (this particular win_rate/payoff combination is highly sensitive
        to small shifts in win_rate, so convergence is genuinely slow -
        the point of this test is the relative improvement, not a fixed
        absolute tolerance)."""
        small = RiskAgent(max_position_size=1.0, max_daily_loss=10.0, kelly_min_trades=10)
        large = RiskAgent(max_position_size=1.0, max_daily_loss=10.0, kelly_min_trades=10, kelly_lookback_trades=200)
        self._feed_trades(small, [0.02] * 6 + [-0.02] * 4)  # 10 trades, 60% win rate
        self._feed_trades(large, [0.02] * 120 + [-0.02] * 80)  # 200 trades, same 60% win rate

        raw_point_estimate_kelly = small.risk_manager.calculate_kelly_fraction(0.6, 0.02, -0.02)
        small_gap = raw_point_estimate_kelly - small._kelly_position_scale()
        large_gap = raw_point_estimate_kelly - large._kelly_position_scale()

        assert large_gap < small_gap


class TestSmallAccountStakeFloor:
    """Regression coverage for the exact scenario a real user hit: a real
    account with $7.70 capital, whose pure risk_per_trade%/max_position_size
    sizing computes a $0.77 position - below Binance's real minimum order
    size ($5+ for most pairs) for every single tracked symbol, meaning the
    account could never place one real trade regardless of signal quality."""

    def test_bumps_size_up_to_min_stake_usd_when_capital_can_afford_it(self):
        # $7.70 capital, default 2%/1% sizing -> $0.77 uncapped by
        # max_position_size, well below the $1 floor - must bump to $1.
        agent = RiskAgent(max_position_size=0.1, risk_per_trade=0.02, stop_loss_pct=0.01, min_stake_usd=1.0)

        verdict = agent.evaluate_buy("DOGE/USDT", price=0.074, capital=7.70, positions_df=_empty_positions())

        assert verdict.approved is True
        assert verdict.quantity == pytest.approx(1.0)

    def test_uses_the_real_exchange_minimum_when_higher_than_min_stake_usd(self):
        # Binance's real minimum for most pairs ($5) is higher than the
        # system's own $1 floor - the larger of the two must win. The
        # exchange minimum also gets a 15% safety margin (see
        # _EXCHANGE_MIN_NOTIONAL_SAFETY_MARGIN in risk.py) so the
        # post-fee, post-lot-step-rounded balance still clears the real
        # minimum: $5.00 * 1.15 = $5.75.
        agent = RiskAgent(max_position_size=0.1, risk_per_trade=0.02, stop_loss_pct=0.01, min_stake_usd=1.0)

        verdict = agent.evaluate_buy(
            "BTC/USDT", price=64000.0, capital=7.70, positions_df=_empty_positions(), exchange_min_notional=5.0
        )

        assert verdict.approved is True
        assert verdict.quantity == pytest.approx(5.75)

    def test_refuses_cleanly_when_capital_cannot_afford_the_floor_with_margin(self):
        # $1.02 capital, $1 floor, 5% margin required -> $1 > $1.02*0.95
        # ($0.969) - genuinely too small, must refuse rather than attempt
        # an order that would likely fail on fees/slippage at the margin.
        agent = RiskAgent(max_position_size=0.1, risk_per_trade=0.02, stop_loss_pct=0.01, min_stake_usd=1.0)

        verdict = agent.evaluate_buy("DOGE/USDT", price=0.074, capital=1.02, positions_df=_empty_positions())

        assert verdict.approved is False
        assert "too small" in verdict.reason

    def test_refuses_cleanly_when_capital_cannot_afford_the_real_exchange_minimum(self):
        agent = RiskAgent(max_position_size=0.1, risk_per_trade=0.02, stop_loss_pct=0.01, min_stake_usd=1.0)

        verdict = agent.evaluate_buy(
            "BTC/USDT", price=64000.0, capital=3.0, positions_df=_empty_positions(), exchange_min_notional=5.0
        )

        assert verdict.approved is False
        assert "5.75" in verdict.reason  # $5.00 real minimum * 1.15 safety margin

    def test_does_not_touch_sizing_once_capital_is_large_enough_to_clear_the_floor_naturally(self):
        # The whole point of "adapts as capital grows": once risk-based
        # sizing already clears the floor on its own, this must be a
        # complete no-op - same result with or without min_stake_usd set.
        with_floor = RiskAgent(max_position_size=0.5, risk_per_trade=0.02, stop_loss_pct=0.01, min_stake_usd=1.0)
        without_floor = RiskAgent(max_position_size=0.5, risk_per_trade=0.02, stop_loss_pct=0.01, min_stake_usd=0.0)

        v1 = with_floor.evaluate_buy("BTC/USDT", price=100.0, capital=10000.0, positions_df=_empty_positions())
        v2 = without_floor.evaluate_buy("BTC/USDT", price=100.0, capital=10000.0, positions_df=_empty_positions())

        assert v1.quantity == pytest.approx(v2.quantity)

    def test_missing_exchange_min_notional_falls_back_to_min_stake_usd_only(self):
        agent = RiskAgent(max_position_size=0.1, risk_per_trade=0.02, stop_loss_pct=0.01, min_stake_usd=1.0)

        verdict = agent.evaluate_buy(
            "DOGE/USDT", price=0.074, capital=7.70, positions_df=_empty_positions(), exchange_min_notional=None
        )

        assert verdict.approved is True
        assert verdict.quantity == pytest.approx(1.0)
