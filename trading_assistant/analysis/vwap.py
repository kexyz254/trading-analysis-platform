"""Session-anchored VWAP (Volume-Weighted Average Price).

Distinct from agents/features.py's existing `price_vs_vwap`, which is a
ROLLING 20-bar VWAP (a smoothed price-level indicator, no concept of a
trading session). This is the institutional-style VWAP: cumulative
typical-price*volume divided by cumulative volume, reset at the start of
each session (calendar day by default), the benchmark many real-world
execution algorithms mean-revert to.

Causal by construction: cumulative sum only ever accumulates the current
and PRIOR rows within the same session - a row's own session VWAP never
depends on a bar that hasn't happened yet, and floor(timestamp) to find
which session a row belongs to only looks at that row's own timestamp.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_SESSION_HOURS = 24


def session_vwap(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series,
    timestamp: pd.Series,
    session_hours: int = DEFAULT_SESSION_HOURS,
) -> pd.Series:
    """Cumulative volume-weighted average price, resetting at the start
    of every `session_hours`-hour block (24 = calendar day, the
    institutional default; 4 fits this system's 1h bars evenly too).
    NaN where cumulative volume in the session so far is zero (no trades
    to weight by yet), not fabricated as 0 or the raw price."""
    typical_price = (high + low + close) / 3.0
    ts = pd.to_datetime(timestamp)
    session_id = ts.dt.floor(f"{session_hours}h")

    pv = typical_price * volume
    cum_pv = pv.groupby(session_id).cumsum()
    cum_volume = volume.groupby(session_id).cumsum()

    return cum_pv / cum_volume.replace(0, np.nan)
