"""Point-in-time universe construction and provider normalization."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Sequence

from .contracts import Instrument, InstrumentKind, MarketTicker, require_unique_instruments


_BINANCE_SPOT_SYMBOL = re.compile(r"^[A-Z0-9_-]{3,40}$")


def _decimal(row: dict[str, Any], key: str, *, default: str = "0") -> float:
    raw = row.get(key, default)
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid numeric field {key}") from exc
    if not value.is_finite():
        raise ValueError(f"non-finite numeric field {key}")
    return float(value)


class UniverseRegistry:
    def __init__(self, *, quote_assets: Iterable[str]) -> None:
        self.quote_assets = frozenset(asset.upper() for asset in quote_assets)
        if not self.quote_assets:
            raise ValueError("quote_assets cannot be empty")

    def from_binance_exchange_info(self, payload: dict[str, Any]) -> tuple[Instrument, ...]:
        rows = payload.get("symbols")
        if not isinstance(rows, list):
            raise ValueError("exchangeInfo symbols must be a list")
        instruments: list[Instrument] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            if row.get("status") != "TRADING":
                continue
            quote = str(row.get("quoteAsset", "")).upper()
            if quote not in self.quote_assets:
                continue
            permissions = row.get("permissions") or []
            if permissions and "SPOT" not in permissions:
                continue
            symbol = str(row.get("symbol", "")).upper()
            base = str(row.get("baseAsset", "")).upper()
            if not symbol or not base:
                continue
            if not _BINANCE_SPOT_SYMBOL.fullmatch(symbol):
                continue
            instruments.append(Instrument(
                instrument_id=f"binance:spot:{symbol}",
                venue="binance",
                venue_symbol=symbol,
                base_asset=base,
                quote_asset=quote,
                kind=InstrumentKind.SPOT,
                status="TRADING",
                price_precision=_optional_int(row.get("quotePrecision")),
                quantity_precision=_optional_int(row.get("baseAssetPrecision")),
            ))
        return require_unique_instruments(sorted(instruments, key=lambda item: item.instrument_id))

    def normalize_rest_tickers(
        self,
        rows: Sequence[dict[str, Any]],
        instruments: Iterable[Instrument],
        *,
        received_at: datetime | None = None,
    ) -> tuple[MarketTicker, ...]:
        received_at = received_at or datetime.now(timezone.utc)
        by_symbol = {instrument.venue_symbol: instrument for instrument in instruments}
        tickers: list[MarketTicker] = []
        seen: set[str] = set()
        for row in rows:
            symbol = str(row.get("symbol", "")).upper()
            instrument = by_symbol.get(symbol)
            if instrument is None or symbol in seen:
                continue
            event_time_ms = row.get("closeTime")
            try:
                event_time = datetime.fromtimestamp(float(event_time_ms) / 1000.0, tz=timezone.utc)
            except (TypeError, ValueError, OSError):
                event_time = received_at
            try:
                ticker = MarketTicker(
                    instrument_id=instrument.instrument_id,
                    venue_symbol=symbol,
                    last_price=_decimal(row, "lastPrice"),
                    open_price_24h=_decimal(row, "openPrice"),
                    high_price_24h=_decimal(row, "highPrice"),
                    low_price_24h=_decimal(row, "lowPrice"),
                    base_volume_24h=_decimal(row, "volume"),
                    quote_volume_24h=_decimal(row, "quoteVolume"),
                    bid_price=_optional_decimal(row, "bidPrice"),
                    ask_price=_optional_decimal(row, "askPrice"),
                    trade_count_24h=_optional_int(row.get("count")),
                    event_time=event_time,
                )
            except ValueError:
                continue
            seen.add(symbol)
            tickers.append(ticker)
        return tuple(sorted(tickers, key=lambda item: item.instrument_id))

    def normalize_mini_tickers(
        self,
        rows: Sequence[dict[str, Any]],
        instruments: Iterable[Instrument],
        *,
        received_at: datetime | None = None,
    ) -> tuple[MarketTicker, ...]:
        received_at = received_at or datetime.now(timezone.utc)
        by_symbol = {instrument.venue_symbol: instrument for instrument in instruments}
        tickers: list[MarketTicker] = []
        for row in rows:
            symbol = str(row.get("s", "")).upper()
            instrument = by_symbol.get(symbol)
            if instrument is None:
                continue
            try:
                event_time = datetime.fromtimestamp(float(row["E"]) / 1000.0, tz=timezone.utc)
                tickers.append(MarketTicker(
                    instrument_id=instrument.instrument_id,
                    venue_symbol=symbol,
                    last_price=_decimal(row, "c"),
                    open_price_24h=_decimal(row, "o"),
                    high_price_24h=_decimal(row, "h"),
                    low_price_24h=_decimal(row, "l"),
                    base_volume_24h=_decimal(row, "v"),
                    quote_volume_24h=_decimal(row, "q"),
                    event_time=event_time,
                ))
            except (KeyError, TypeError, ValueError, OSError):
                continue
        return tuple(tickers)


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_decimal(row: dict[str, Any], key: str) -> float | None:
    if row.get(key) in (None, ""):
        return None
    try:
        return _decimal(row, key)
    except ValueError:
        return None
