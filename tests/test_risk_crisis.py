from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from trading_assistant.risk.crisis import CrisisState


def _frame(prices, start="2026-01-01T00:00:00Z", freq="10min"):
    n = len(prices)
    return pd.DataFrame({
        "timestamp": pd.date_range(start, periods=n, freq=freq, tz="UTC"),
        "close": prices,
    })


class TestCrisisStateUpdate:
    def test_triggers_on_a_sharp_drop_within_the_window(self):
        # 7 bars * 10min = exactly 1h span; price falls from 100 to 80 (20% drop)
        frame = _frame([100.0, 98.0, 95.0, 90.0, 85.0, 82.0, 80.0])
        state = CrisisState()

        state.update(frame, drop_threshold=0.15, window=timedelta(hours=1))

        assert state.triggered_at is not None
        assert state.reference_price == pytest.approx(100.0)

    def test_does_not_trigger_on_a_mild_drop(self):
        frame = _frame([100.0, 99.0, 98.0, 97.0, 96.0, 95.0])  # 5% drop
        state = CrisisState()

        state.update(frame, drop_threshold=0.15, window=timedelta(hours=1))

        assert state.triggered_at is None

    def test_does_not_trigger_without_enough_history_to_know_the_window_start(self):
        frame = _frame([100.0, 50.0])  # huge drop, but only 10 minutes of history - can't confirm a 1h window
        state = CrisisState()

        state.update(frame, drop_threshold=0.15, window=timedelta(hours=1))

        assert state.triggered_at is None

    def test_already_triggered_state_is_not_overwritten_by_a_second_update(self):
        """A prolonged decline must not keep resetting reference_price -
        that would create a rolling target that never lets recovery clear it."""
        frame = _frame([100.0, 98.0, 95.0, 90.0, 85.0, 82.0, 80.0])
        state = CrisisState()
        state.update(frame, drop_threshold=0.15, window=timedelta(hours=1))
        first_reference = state.reference_price
        first_triggered_at = state.triggered_at

        worse_frame = _frame([80.0, 78.0, 75.0, 70.0, 65.0, 62.0, 60.0])
        state.update(worse_frame, drop_threshold=0.15, window=timedelta(hours=1))

        assert state.reference_price == first_reference
        assert state.triggered_at == first_triggered_at


class TestCrisisStateIsActive:
    def test_inactive_when_never_triggered(self):
        state = CrisisState()
        assert state.is_active(current_price=100.0, drop_threshold=0.15, max_duration=timedelta(hours=24)) is False

    def test_active_immediately_after_triggering(self):
        now = datetime.now(timezone.utc)
        state = CrisisState(triggered_at=now, reference_price=100.0)

        # 84.0 is still (just) below the 85.0 recovery threshold (100 * (1-0.15))
        assert state.is_active(current_price=84.0, drop_threshold=0.15, max_duration=timedelta(hours=24), now=now) is True

    def test_clears_after_max_duration_elapses(self):
        triggered_at = datetime.now(timezone.utc) - timedelta(hours=25)
        state = CrisisState(triggered_at=triggered_at, reference_price=100.0)

        active = state.is_active(
            current_price=85.0, drop_threshold=0.15, max_duration=timedelta(hours=24),
            now=datetime.now(timezone.utc),
        )

        assert active is False
        assert state.triggered_at is None  # cleared itself

    def test_clears_on_recovery_above_threshold(self):
        now = datetime.now(timezone.utc)
        state = CrisisState(triggered_at=now, reference_price=100.0)

        # recovered to 90 - within 15% of the original 100 reference
        active = state.is_active(current_price=90.0, drop_threshold=0.15, max_duration=timedelta(hours=24), now=now)

        assert active is False
        assert state.triggered_at is None

    def test_stays_active_while_still_down_and_within_duration(self):
        now = datetime.now(timezone.utc)
        state = CrisisState(triggered_at=now - timedelta(hours=2), reference_price=100.0)

        active = state.is_active(current_price=82.0, drop_threshold=0.15, max_duration=timedelta(hours=24), now=now)

        assert active is True
