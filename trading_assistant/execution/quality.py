"""Execution quality: slippage and market-impact measurement.

Before this, the system assumed a FIXED slippage_rate for backtesting
purposes but never checked what slippage real orders actually
experienced - the exchange's own reported fill price versus the price a
trade was decided on was already being resolved (see
ExecutionAgent._resolve_fill), but only ever used for portfolio
bookkeeping, never measured and tracked as its own metric the way a
professional execution desk would.
"""

from __future__ import annotations

from typing import Optional


def compute_slippage_pct(intended_price: float, fill_price: float, side: str) -> Optional[float]:
    """Positive = worse than intended (paid more on a buy, received less
    on a sell); negative = better than intended (price improvement). The
    sign of "worse" flips between buy and sell, since a higher fill price
    helps a seller but hurts a buyer."""
    if intended_price <= 0:
        return None
    raw_pct = (fill_price - intended_price) / intended_price
    return raw_pct if side == "buy" else -raw_pct


def order_size_vs_volume_pct(order_notional: float, recent_avg_volume_notional: float) -> Optional[float]:
    """A proxy for market-impact risk: what fraction of the symbol's own
    recent average bar volume this single order represents. Full order-
    book depth isn't available from Binance's REST OHLCV history, so this
    uses realized volume as the best available liquidity proxy - a large
    order relative to typical volume is a real market-impact risk signal
    even without direct book-depth data."""
    if recent_avg_volume_notional is None or recent_avg_volume_notional <= 0:
        return None
    if order_notional < 0:
        return None
    return order_notional / recent_avg_volume_notional
