"""Validated point-in-time observations used by deterministic UMIL engines."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable


def _finite(name: str, value: float) -> float:
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


@dataclass(frozen=True, slots=True)
class Candle:
    instrument_id: str
    interval: str
    open_time: datetime
    close_time: datetime
    open: float
    high: float
    low: float
    close: float
    base_volume: float
    quote_volume: float
    trade_count: int
    taker_buy_quote_volume: float
    closed: bool = True

    def __post_init__(self) -> None:
        if self.open_time.tzinfo is None or self.close_time.tzinfo is None:
            raise ValueError("candle timestamps must be timezone-aware")
        if self.close_time <= self.open_time:
            raise ValueError("candle close_time must follow open_time")
        values = (self.open, self.high, self.low, self.close)
        if any(_finite("price", value) <= 0 for value in values):
            raise ValueError("candle prices must be positive")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("candle OHLC relationship is invalid")
        if self.high < self.low:
            raise ValueError("candle high cannot be below low")
        if min(self.base_volume, self.quote_volume, self.taker_buy_quote_volume) < 0:
            raise ValueError("candle volumes cannot be negative")
        if self.taker_buy_quote_volume > self.quote_volume + 1e-9:
            raise ValueError("taker buy volume cannot exceed quote volume")
        if self.trade_count < 0:
            raise ValueError("trade_count cannot be negative")


@dataclass(frozen=True, slots=True)
class AggTrade:
    instrument_id: str
    trade_id: int
    event_time: datetime
    price: float
    quantity: float
    buyer_is_maker: bool

    def __post_init__(self) -> None:
        if self.event_time.tzinfo is None:
            raise ValueError("trade event_time must be timezone-aware")
        if _finite("price", self.price) <= 0 or _finite("quantity", self.quantity) <= 0:
            raise ValueError("trade price and quantity must be positive")

    @property
    def quote_notional(self) -> float:
        return self.price * self.quantity

    @property
    def signed_notional(self) -> float:
        return -self.quote_notional if self.buyer_is_maker else self.quote_notional


@dataclass(frozen=True, slots=True)
class BookLevel:
    price: float
    quantity: float

    def __post_init__(self) -> None:
        if _finite("price", self.price) <= 0 or _finite("quantity", self.quantity) < 0:
            raise ValueError("book level is invalid")


@dataclass(frozen=True, slots=True)
class OrderBookSnapshot:
    instrument_id: str
    event_time: datetime
    last_update_id: int
    bids: tuple[BookLevel, ...]
    asks: tuple[BookLevel, ...]

    def __post_init__(self) -> None:
        if self.event_time.tzinfo is None:
            raise ValueError("book event_time must be timezone-aware")
        if not self.bids or not self.asks:
            raise ValueError("book requires both sides")
        if max(level.price for level in self.bids) >= min(level.price for level in self.asks):
            raise ValueError("crossed or locked order book")


@dataclass(frozen=True, slots=True)
class DerivativesObservation:
    instrument_id: str
    observed_at: datetime
    mark_price: float
    index_price: float
    funding_rate: float
    open_interest: float
    previous_open_interest: float | None = None

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None:
            raise ValueError("derivatives timestamp must be timezone-aware")
        if min(_finite("mark_price", self.mark_price), _finite("index_price", self.index_price)) <= 0:
            raise ValueError("derivatives prices must be positive")
        if _finite("open_interest", self.open_interest) < 0:
            raise ValueError("open interest cannot be negative")


@dataclass(frozen=True, slots=True)
class AssetScaleObservation:
    asset_id: str
    observed_at: datetime
    market_cap_usd: float | None
    fully_diluted_value_usd: float | None
    circulating_supply: float | None
    rank: int | None
    sector: str | None = None

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None:
            raise ValueError("scale timestamp must be timezone-aware")
        for name in ("market_cap_usd", "fully_diluted_value_usd", "circulating_supply"):
            value = getattr(self, name)
            if value is not None and _finite(name, value) < 0:
                raise ValueError(f"{name} cannot be negative")
        if self.rank is not None and self.rank <= 0:
            raise ValueError("rank must be positive")


def only_closed(candles: Iterable[Candle]) -> tuple[Candle, ...]:
    rows = tuple(sorted((row for row in candles if row.closed), key=lambda row: row.close_time))
    if len({row.close_time for row in rows}) != len(rows):
        raise ValueError("duplicate closed-candle boundary")
    return rows
