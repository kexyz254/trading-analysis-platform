"""Strict normalization for public candles, trades, and books."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Sequence

from .observations import AggTrade, BookLevel, Candle, OrderBookSnapshot


def _number(value: Any) -> float:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("invalid provider number") from exc
    if not result.is_finite():
        raise ValueError("non-finite provider number")
    return float(result)


def normalize_binance_klines(
    instrument_id: str, interval: str, rows: Sequence[Sequence[Any]], *, as_of: datetime,
) -> tuple[Candle, ...]:
    candles: list[Candle] = []
    for row in rows:
        if len(row) < 11:
            continue
        try:
            open_time = datetime.fromtimestamp(float(row[0]) / 1000.0, tz=timezone.utc)
            close_time = datetime.fromtimestamp(float(row[6]) / 1000.0, tz=timezone.utc)
            candle = Candle(
                instrument_id=instrument_id, interval=interval,
                open_time=open_time, close_time=close_time,
                open=_number(row[1]), high=_number(row[2]), low=_number(row[3]),
                close=_number(row[4]), base_volume=_number(row[5]),
                quote_volume=_number(row[7]), trade_count=int(row[8]),
                taker_buy_quote_volume=_number(row[10]), closed=close_time < as_of,
            )
        except (TypeError, ValueError, OSError):
            continue
        candles.append(candle)
    return tuple(sorted(candles, key=lambda item: item.close_time))


def normalize_binance_trades(
    instrument_id: str, rows: Iterable[dict[str, Any]],
) -> tuple[AggTrade, ...]:
    trades = []
    for row in rows:
        try:
            trades.append(AggTrade(
                instrument_id=instrument_id, trade_id=int(row["a"]),
                event_time=datetime.fromtimestamp(float(row["T"]) / 1000.0, tz=timezone.utc),
                price=_number(row["p"]), quantity=_number(row["q"]),
                buyer_is_maker=bool(row["m"]),
            ))
        except (KeyError, TypeError, ValueError, OSError):
            continue
    return tuple(sorted(trades, key=lambda item: (item.event_time, item.trade_id)))


def normalize_binance_depth(
    instrument_id: str, payload: dict[str, Any], *, observed_at: datetime,
) -> OrderBookSnapshot:
    def levels(rows: Iterable[Sequence[Any]]) -> tuple[BookLevel, ...]:
        output = []
        for row in rows:
            if len(row) >= 2:
                output.append(BookLevel(_number(row[0]), _number(row[1])))
        return tuple(output)
    return OrderBookSnapshot(
        instrument_id=instrument_id, event_time=observed_at,
        last_update_id=int(payload["lastUpdateId"]),
        bids=levels(payload.get("bids", ())), asks=levels(payload.get("asks", ())),
    )
