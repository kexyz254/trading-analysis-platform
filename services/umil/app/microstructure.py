"""Trade-flow and order-book intelligence with explicit evidence windows."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Iterable

from .observations import AggTrade, OrderBookSnapshot


@dataclass(frozen=True, slots=True)
class FlowMetrics:
    as_of: str
    window_seconds: int
    trade_count: int
    buy_notional: float
    sell_notional: float
    taker_imbalance: float | None
    cumulative_volume_delta: float
    price_change_pct: float | None
    signed_flow_efficiency: float | None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class BookMetrics:
    as_of: str
    spread_bps: float
    depth_imbalance: float
    bid_depth_quote: float
    ask_depth_quote: float
    buy_slippage_bps: float | None
    sell_slippage_bps: float | None
    resilience_score: float

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PressureAssessment:
    score: float
    label: str
    confidence: float
    evidence: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        row = asdict(self)
        row["evidence"] = list(self.evidence)
        return row


def calculate_trade_flow(
    trades: Iterable[AggTrade], *, as_of: datetime, window_seconds: int = 900,
) -> FlowMetrics:
    if as_of.tzinfo is None or window_seconds <= 0:
        raise ValueError("valid as_of and window are required")
    cutoff = as_of - timedelta(seconds=window_seconds)
    rows = tuple(sorted(
        (trade for trade in trades if cutoff <= trade.event_time <= as_of),
        key=lambda trade: (trade.event_time, trade.trade_id),
    ))
    buys = sum(row.quote_notional for row in rows if not row.buyer_is_maker)
    sells = sum(row.quote_notional for row in rows if row.buyer_is_maker)
    total = buys + sells
    imbalance = (buys - sells) / total if total > 0 else None
    price_change = (
        (rows[-1].price / rows[0].price - 1.0) * 100.0 if len(rows) > 1 else None
    )
    efficiency = None
    if imbalance is not None and price_change is not None and abs(imbalance) > 0.02:
        efficiency = math.copysign(abs(price_change) / max(abs(imbalance), 0.05), imbalance)
    return FlowMetrics(
        as_of=as_of.isoformat(), window_seconds=window_seconds, trade_count=len(rows),
        buy_notional=buys, sell_notional=sells, taker_imbalance=imbalance,
        cumulative_volume_delta=buys - sells, price_change_pct=price_change,
        signed_flow_efficiency=efficiency,
    )


def _slippage(levels, *, quote_notional: float, midpoint: float, buy: bool) -> float | None:
    remaining = quote_notional
    acquired = 0.0
    spent = 0.0
    for level in levels:
        available_quote = level.price * level.quantity
        consumed = min(remaining, available_quote)
        acquired += consumed / level.price
        spent += consumed
        remaining -= consumed
        if remaining <= 1e-9:
            break
    if remaining > 1e-6 or acquired <= 0:
        return None
    average = spent / acquired
    raw = (average / midpoint - 1.0) * 10_000.0
    return raw if buy else -raw


def calculate_book_metrics(
    book: OrderBookSnapshot, *, target_quote_notional: float = 10_000.0,
) -> BookMetrics:
    if target_quote_notional <= 0:
        raise ValueError("target_quote_notional must be positive")
    best_bid = max(level.price for level in book.bids)
    best_ask = min(level.price for level in book.asks)
    midpoint = (best_bid + best_ask) / 2.0
    spread = (best_ask - best_bid) / midpoint * 10_000.0
    bid_depth = sum(level.price * level.quantity for level in book.bids)
    ask_depth = sum(level.price * level.quantity for level in book.asks)
    total_depth = bid_depth + ask_depth
    imbalance = (bid_depth - ask_depth) / total_depth if total_depth else 0.0
    buy_slippage = _slippage(
        sorted(book.asks, key=lambda row: row.price),
        quote_notional=target_quote_notional, midpoint=midpoint, buy=True,
    )
    sell_slippage = _slippage(
        sorted(book.bids, key=lambda row: row.price, reverse=True),
        quote_notional=target_quote_notional, midpoint=midpoint, buy=False,
    )
    penalty = spread
    if buy_slippage is not None:
        penalty += buy_slippage
    if sell_slippage is not None:
        penalty += sell_slippage
    resilience = max(0.0, min(100.0, 100.0 - penalty * 2.0))
    return BookMetrics(
        as_of=book.event_time.isoformat(), spread_bps=spread,
        depth_imbalance=imbalance, bid_depth_quote=bid_depth,
        ask_depth_quote=ask_depth, buy_slippage_bps=buy_slippage,
        sell_slippage_bps=sell_slippage, resilience_score=resilience,
    )


def assess_pressure(
    flow: FlowMetrics | None, book: BookMetrics | None,
) -> PressureAssessment:
    components: list[tuple[float, float, str]] = []
    if flow is not None and flow.taker_imbalance is not None:
        weight = min(0.65, 0.25 + math.log10(max(1, flow.trade_count)) / 10.0)
        components.append((flow.taker_imbalance, weight, "TAKER_IMBALANCE"))
    if book is not None:
        components.append((book.depth_imbalance, 0.35, "DEPTH_IMBALANCE"))
    if not components:
        return PressureAssessment(50.0, "INSUFFICIENT_EVIDENCE", 0.0, ())
    denominator = sum(weight for _, weight, _ in components)
    signed = sum(value * weight for value, weight, _ in components) / denominator
    score = max(0.0, min(100.0, 50.0 + signed * 50.0))
    if score >= 60:
        label = "BUYER_DOMINANT"
    elif score <= 40:
        label = "SELLER_DOMINANT"
    else:
        label = "BALANCED"
    confidence = min(100.0, denominator / 1.0 * 100.0)
    return PressureAssessment(score, label, confidence, tuple(item[2] for item in components))
