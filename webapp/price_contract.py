"""Prediction-contract lifecycle for the Price Call Engine.

The Price Call Engine no longer anchors a prediction to whatever the spot
price happened to be at the arbitrary moment a background tick fired. Instead
it synchronizes to **prediction contracts**: fixed, clock-aligned rounds
(every 5m, every 15m) that mirror how a Binance-style price-prediction product
defines a round. Each contract has:

  * a stable contract_id (deterministic from symbol + horizon + open time),
  * an open time and a close time (millisecond precision, UTC epoch ms),
  * a LOCKED "Price to Beat" - the reference price at the instant the contract
    opened, captured once and never moved as spot drifts, and
  * optionally the exchange server time observed when the contract was locked.

The engine predicts UP/DOWN relative to that locked Price to Beat, and at
close settles the real price against the SAME locked reference - so the
question a call answers ("will the price be above/below the Price to Beat when
this round closes?") is exactly the question a prediction contract poses.

Design: `ContractProvider` is the seam. `WallClockContractProvider` derives
the contract from clock-aligned candles and uses the horizon candle's OPEN as
the Price to Beat - real, exchange-provided, and identical to the reference a
5m/15m round would lock. A future provider that reads a real Binance
prediction-contract feed (WebSocket preferred, REST fallback) can drop in
behind the same interface without the engine changing; that provider is
intentionally not fabricated here since it depends on an endpoint this
deployment may not have. The engine treats WebSocket-vs-polling as an
implementation detail of the provider.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def ms_to_iso(ms: int) -> str:
    """UTC ISO-8601 for an epoch-millisecond timestamp."""
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat()


@dataclass(frozen=True)
class Contract:
    """One prediction round for a (symbol, horizon), with its Price to Beat and
    timing locked at open. Immutable - once locked, nothing here moves."""

    symbol: str
    horizon_label: str          # "5m" / "15m"
    horizon_seconds: int
    contract_id: str            # deterministic: f"{symbol}|{horizon}|{open_ms}"
    open_time_ms: int           # round open (clock boundary), epoch ms UTC
    close_time_ms: int          # round close / settlement, epoch ms UTC
    price_to_beat: float        # reference price locked at open
    locked_at_ms: int           # when this engine observed & locked the round
    server_time_ms: Optional[int]  # exchange server clock at lock, if available
    source: str                 # "wallclock" | "binance_ws" | "binance_rest"

    @property
    def open_iso(self) -> str:
        return ms_to_iso(self.open_time_ms)

    @property
    def close_iso(self) -> str:
        return ms_to_iso(self.close_time_ms)

    @property
    def lock_latency_ms(self) -> int:
        """How long after the round opened we locked it - the synchronization
        error. Small = we caught the rollover promptly."""
        return self.locked_at_ms - self.open_time_ms


@runtime_checkable
class ContractProvider(Protocol):
    """Source of the current prediction contract for a (symbol, horizon), and
    of a contract's settlement price once it has closed."""

    def current_contract(
        self, symbol: str, horizon_seconds: int, horizon_label: str
    ) -> Optional[Contract]:
        ...

    def settlement_price(
        self, symbol: str, horizon_label: str, open_time_ms: int, close_time_ms: int
    ) -> Optional[float]:
        ...


KlineFetch = Callable[[str, str, int], List[Dict[str, Any]]]
ServerTimeFetch = Callable[[], Optional[int]]


class WallClockContractProvider:
    """Derives prediction contracts from clock-aligned exchange candles.

    A 5m contract is the round [T, T+5m) where T is the current 5-minute
    boundary; its Price to Beat is the OPEN of the 5m candle that started at T
    (the real, exchange-recorded price at the round's open instant). Settlement
    is that same candle's CLOSE once the round has ended. This needs no special
    endpoint and lines up exactly with how a clock-aligned prediction round
    locks its reference and resolves.
    """

    def __init__(
        self,
        kline_fetch: KlineFetch,
        *,
        server_time_fetch: Optional[ServerTimeFetch] = None,
        now_ms_fn: Callable[[], int] = _now_ms,
        fetch_limit: int = 6,
    ):
        self._fetch = kline_fetch
        self._server_time = server_time_fetch
        self._now_ms = now_ms_fn
        # A handful of the most recent candles is enough to hold both the
        # in-progress round and the just-closed one we settle.
        self._fetch_limit = fetch_limit

    def _boundary_ms(self, now_ms: int, horizon_seconds: int) -> int:
        step = horizon_seconds * 1000
        return (now_ms // step) * step

    def _candle_by_open(
        self, symbol: str, horizon_label: str, open_time_ms: int
    ) -> Optional[Dict[str, Any]]:
        try:
            klines = self._fetch(symbol, horizon_label, self._fetch_limit)
        except Exception:  # noqa: BLE001 - a fetch failure is "no contract yet", not a crash
            logger.exception("kline fetch failed for %s %s", symbol, horizon_label)
            return None
        if not klines:
            return None
        for k in klines:
            if int(k["timestamp"]) == open_time_ms:
                return k
        return None

    def current_contract(
        self, symbol: str, horizon_seconds: int, horizon_label: str
    ) -> Optional[Contract]:
        now_ms = self._now_ms()
        open_ms = self._boundary_ms(now_ms, horizon_seconds)
        close_ms = open_ms + horizon_seconds * 1000

        candle = self._candle_by_open(symbol, horizon_label, open_ms)
        if candle is None:
            # The exchange has not published the current round's candle yet
            # (or the fetch failed). No reference price to lock -> no contract
            # this tick; the next tick retries.
            return None

        price_to_beat = float(candle["open"])
        if price_to_beat <= 0:
            return None

        server_ms: Optional[int] = None
        if self._server_time is not None:
            try:
                server_ms = self._server_time()
            except Exception:  # noqa: BLE001 - server time is best-effort metadata
                server_ms = None

        contract = Contract(
            symbol=symbol,
            horizon_label=horizon_label,
            horizon_seconds=horizon_seconds,
            contract_id=f"{symbol}|{horizon_label}|{open_ms}",
            open_time_ms=open_ms,
            close_time_ms=close_ms,
            price_to_beat=price_to_beat,
            locked_at_ms=now_ms,
            server_time_ms=server_ms,
            source="wallclock",
        )
        return contract

    def settlement_price(
        self, symbol: str, horizon_label: str, open_time_ms: int, close_time_ms: int
    ) -> Optional[float]:
        # The round resolves to the CLOSE of its own candle, available only
        # once the round has actually ended.
        if self._now_ms() < close_time_ms:
            return None
        candle = self._candle_by_open(symbol, horizon_label, open_time_ms)
        if candle is None:
            return None
        close = float(candle["close"])
        return close if close > 0 else None
