"""Crisis correlation override: a blunt safety overlay on top of
risk/correlation.py's rolling Pearson correlation gate, not a replacement
for it.

Rolling correlation is estimated from calm-period data and systematically
understates how correlated assets become in a genuine market-wide panic -
crypto altcoins that show 60-70% correlation to BTC in normal conditions
can move in lockstep (effective correlation near 1.0) during a crash, and
the rolling estimate (backward-looking over correlation_lookback_bars)
won't catch up until well after the fact. When BTC itself drops sharply
and fast, this assumes the worst about everything else's correlation to it
for a bounded window, rather than trusting a correlation matrix that's
about to be proven wrong by the same event.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd


@dataclass
class CrisisState:
    """Tracks whether the crisis correlation override is currently active.
    Deliberately a plain mutable object (not a pure function return value)
    - RiskAgent needs to remember "we're still in the window" across many
    cycles without external state management, the same way CircuitBreaker
    tracks its own trip state on the instance."""

    triggered_at: Optional[datetime] = None
    reference_price: Optional[float] = None  # the crisis symbol's price right before the drop that triggered this

    def update(
        self,
        crisis_symbol_frame: Optional[pd.DataFrame],
        drop_threshold: float,
        window: timedelta,
        now: Optional[datetime] = None,
    ) -> None:
        """crisis_symbol_frame: the crisis symbol's (e.g. BTC/USDT) OHLCV
        frame with 'timestamp' and 'close' columns, same shape every other
        OHLCV consumer in this codebase already takes - deliberately NOT a
        bare pandas Series, since price_history's Series (as built in
        orchestrator.py) uses a plain positional index, not a timestamp
        one. Compares the LATEST close against the last known close AT OR
        BEFORE `window` ago - an acute, just-happened check (same
        reasoning as classify_market_condition's "latest bar, not a
        windowed average" - a crisis flag that only reacted to a crash
        from days ago would be pointless). A second call while already
        triggered is a no-op: only a FRESH crash (after the previous one
        has cleared) can re-trigger, so one prolonged decline doesn't keep
        resetting reference_price into a rolling target that never lets
        the override actually clear via recovery.
        """
        if self.triggered_at is not None or crisis_symbol_frame is None or len(crisis_symbol_frame) < 2:
            return
        now = now or datetime.now(timezone.utc)
        frame = crisis_symbol_frame
        timestamps = pd.to_datetime(frame["timestamp"], utc=True)
        latest_price = float(frame["close"].iloc[-1])
        latest_time = timestamps.iloc[-1]
        window_start = latest_time - window
        prior = frame.loc[timestamps <= window_start]
        if prior.empty:
            return  # not enough history yet to know what "one window ago" was
        reference_price = float(prior["close"].iloc[-1])
        if reference_price <= 0:
            return
        drop = (reference_price - latest_price) / reference_price
        if drop >= drop_threshold:
            self.triggered_at = now
            self.reference_price = reference_price

    def is_active(
        self,
        current_price: Optional[float],
        drop_threshold: float,
        max_duration: timedelta,
        now: Optional[datetime] = None,
    ) -> bool:
        """Clears itself (mutates triggered_at/reference_price back to
        None) the moment either condition is met - callers never need to
        separately call a "clear" method. Recovery is checked against the
        ORIGINAL reference_price (not a rolling one - see update's
        docstring), so "recovered" means genuinely back within
        drop_threshold of where the crash started, not just off its worst
        point."""
        if self.triggered_at is None:
            return False
        now = now or datetime.now(timezone.utc)
        if now - self.triggered_at >= max_duration:
            self.triggered_at = None
            self.reference_price = None
            return False
        if (
            current_price is not None
            and self.reference_price is not None
            and current_price >= self.reference_price * (1 - drop_threshold)
        ):
            self.triggered_at = None
            self.reference_price = None
            return False
        return True
