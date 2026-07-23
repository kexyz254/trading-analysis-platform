"""UMIL lifecycle orchestration, deliberately independent from ATS Core."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Sequence

from .anomaly_calibration import calibrate_anomaly
from .breadth import calculate_universe_breadth
from .config import Settings
from .contracts import Instrument, MarketTicker, OperationalState
from .evidence import EvidenceLedger
from .derivatives import calculate_derivatives_metrics
from .derivatives_normalization import normalize_binance_futures_context
from .intelligence import IntelligenceEngine
from .lightweight import calculate_lightweight_states
from .provider_normalization import (
    normalize_binance_depth,
    normalize_binance_klines,
    normalize_binance_trades,
)
from .providers.base import PublicMarketProvider
from .providers.binance_spot import BinanceSpotPublicProvider
from .providers.binance_futures import BinanceFuturesPublicProvider
from .providers.binance_stream import BinanceAllMarketStream
from .providers.coingecko import CoinGeckoPublicProvider
from .scale import normalize_market_scale
from .ticker_book import LiveTickerBook
from .tiering import assign_tiers
from .universe import UniverseRegistry
from .versions import VERSIONS


logger = logging.getLogger(__name__)


class UniverseIntelligenceService:
    def __init__(
        self,
        settings: Settings,
        *,
        provider: PublicMarketProvider | None = None,
        ledger: EvidenceLedger | None = None,
        derivatives_provider: Any | None = None,
        scale_provider: Any | None = None,
    ) -> None:
        self.settings = settings
        self.provider = provider or BinanceSpotPublicProvider(
            timeout_seconds=settings.provider_timeout_seconds
        )
        self.ledger = ledger or EvidenceLedger(settings.database_path)
        self.derivatives_provider = derivatives_provider or BinanceFuturesPublicProvider(
            timeout_seconds=settings.provider_timeout_seconds
        )
        self.scale_provider = scale_provider or CoinGeckoPublicProvider(
            timeout_seconds=settings.provider_timeout_seconds
        )
        self.registry = UniverseRegistry(quote_assets=settings.quote_assets)
        self.ticker_book = LiveTickerBook()
        self.intelligence = IntelligenceEngine(self.ledger)
        self._instruments: tuple[Instrument, ...] = ()
        self._tasks: list[asyncio.Task[Any]] = []
        self._stop_event = asyncio.Event()
        self._provider_state = "INITIALIZING"
        self._provider_reason: str | None = None
        self._provider_latency_ms: dict[str, int] = {}
        self._sequence_gap_recoveries_total = 0
        self._recovery_times: deque[datetime] = deque()
        self._started_at: datetime | None = None
        self._last_snapshot_at: datetime | None = None
        self._intelligence_cursor = 0
        self._intelligence_failures: dict[str, str] = {}
        self._last_intelligence_at: datetime | None = None
        self._previous_open_interest: dict[str, float] = {}
        self._scale_by_asset: dict[str, Any] = {}
        self._ambiguous_scale_assets: frozenset[str] = frozenset()
        self._scale_state = "WARMING"
        self._scale_reason: str | None = None
        self._last_scale_at: datetime | None = None
        self._last_full_evidence_verification_monotonic = 0.0

    async def start(self) -> None:
        if self._tasks:
            return
        self._started_at = datetime.now(timezone.utc)
        await self.bootstrap()
        stream = BinanceAllMarketStream(
            on_tickers=self._on_stream_tickers,
            on_state=self._on_stream_state,
        )
        self._tasks = [
            asyncio.create_task(stream.run(self._stop_event), name="umil-binance-stream"),
            asyncio.create_task(self._snapshot_loop(), name="umil-snapshot-loop"),
            asyncio.create_task(self._metadata_loop(), name="umil-metadata-loop"),
            asyncio.create_task(
                self._evidence_verification_loop(), name="umil-evidence-verification-loop"
            ),
        ]
        if hasattr(self.provider, "fetch_klines"):
            self._tasks.append(asyncio.create_task(
                self._intelligence_loop(), name="umil-intelligence-loop"
            ))
        if self.settings.enable_scale_provider:
            self._tasks.append(asyncio.create_task(
                self._scale_loop(), name="umil-scale-loop"
            ))

    async def stop(self) -> None:
        self._stop_event.set()
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def _evidence_verification_loop(self) -> None:
        """Verify new evidence off the request path and deep-scan periodically."""
        while not self._stop_event.is_set():
            now = time.monotonic()
            full = (
                self._last_full_evidence_verification_monotonic == 0.0
                or now - self._last_full_evidence_verification_monotonic
                >= self.settings.evidence_full_verification_seconds
            )
            try:
                valid, error = await asyncio.to_thread(
                    self.ledger.verify_chain,
                    full=full,
                    batch_size=self.settings.evidence_verification_batch_size,
                )
                if full and valid:
                    self._last_full_evidence_verification_monotonic = time.monotonic()
                if not valid:
                    logger.error("UMIL evidence verification failed: %s", error)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - health audit isolation boundary
                logger.exception("UMIL evidence verification task failed")

            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.settings.evidence_verification_interval_seconds,
                )
            except TimeoutError:
                pass

    async def bootstrap(self) -> None:
        """Load authoritative membership, then seed the live book from REST."""
        started = time.monotonic()
        try:
            metadata = await asyncio.to_thread(self.provider.fetch_exchange_info)
            self._provider_latency_ms[f"{self.provider.name}_metadata"] = int(
                (time.monotonic() - started) * 1000
            )
            instruments = self.registry.from_binance_exchange_info(metadata)
            observed_at = datetime.now(timezone.utc)
            evidence = self.ledger.append(
                event_type="UNIVERSE_MEMBERSHIP_SNAPSHOT",
                payload={
                    "venue": "binance",
                    "kind": "spot",
                    "quote_assets": sorted(self.settings.quote_assets),
                    "instrument_count": len(instruments),
                    "instruments": [row.as_dict() for row in instruments],
                    "versions": VERSIONS.as_dict(),
                },
                observed_at=observed_at,
            )
            self.ledger.replace_universe_projection(instruments, evidence.evidence_id, observed_at)
            self.ledger.set_projection("universe:binance:spot", evidence.evidence_id)
            self._instruments = instruments
        except Exception as exc:  # noqa: BLE001 - provider isolation boundary
            self._provider_state = "UNAVAILABLE"
            self._provider_reason = type(exc).__name__
            logger.exception("UMIL universe bootstrap failed")
            return

        started = time.monotonic()
        try:
            rows = await asyncio.to_thread(self.provider.fetch_all_tickers)
            received_at = datetime.now(timezone.utc)
            self._provider_latency_ms[f"{self.provider.name}_ticker_bootstrap"] = int(
                (time.monotonic() - started) * 1000
            )
            tickers = self.registry.normalize_rest_tickers(
                rows, self._instruments, received_at=received_at
            )
            self.ticker_book.apply(tickers, received_at=received_at)
            self._provider_state = "BOOTSTRAPPED"
            self._provider_reason = None
            await self.create_breadth_snapshot(as_of=received_at)
        except Exception as exc:  # noqa: BLE001 - partial bootstrap remains explicit
            self._provider_state = "DEGRADED"
            self._provider_reason = type(exc).__name__
            logger.exception("UMIL ticker bootstrap failed")

    async def refresh_universe(self) -> None:
        started = time.monotonic()
        metadata = await asyncio.to_thread(self.provider.fetch_exchange_info)
        self._provider_latency_ms[f"{self.provider.name}_metadata"] = int(
            (time.monotonic() - started) * 1000
        )
        instruments = self.registry.from_binance_exchange_info(metadata)
        observed_at = datetime.now(timezone.utc)
        evidence = self.ledger.append(
            event_type="UNIVERSE_MEMBERSHIP_SNAPSHOT",
            payload={
                "venue": "binance",
                "kind": "spot",
                "quote_assets": sorted(self.settings.quote_assets),
                "instrument_count": len(instruments),
                "instruments": [row.as_dict() for row in instruments],
                "versions": VERSIONS.as_dict(),
            },
            observed_at=observed_at,
        )
        self.ledger.replace_universe_projection(instruments, evidence.evidence_id, observed_at)
        self.ledger.set_projection("universe:binance:spot", evidence.evidence_id)
        self._instruments = instruments

    async def _on_stream_tickers(self, rows: Sequence[dict[str, Any]]) -> None:
        received_at = datetime.now(timezone.utc)
        tickers = self.registry.normalize_mini_tickers(
            rows, self._instruments, received_at=received_at
        )
        accepted = self.ticker_book.apply(tickers, received_at=received_at)
        if accepted:
            self._provider_state = "LIVE"
            self._provider_reason = None

    async def _on_stream_state(self, state: str, reason: str | None) -> None:
        previous_state = self._provider_state
        if state == "RECOVERING" and previous_state != "RECOVERING":
            now = datetime.now(timezone.utc)
            self._sequence_gap_recoveries_total += 1
            self._recovery_times.append(now)
        if state == "LIVE" and previous_state == "RECOVERING":
            # The all-market stream contains only changed symbols. Re-seed the
            # projection after any connection gap before declaring it live.
            await self._reconcile_ticker_projection()
        self._provider_state = state
        self._provider_reason = reason

    async def _reconcile_ticker_projection(self) -> None:
        started = time.monotonic()
        rows = await asyncio.to_thread(self.provider.fetch_all_tickers)
        received_at = datetime.now(timezone.utc)
        self._provider_latency_ms[f"{self.provider.name}_ticker_recovery"] = int(
            (time.monotonic() - started) * 1000
        )
        tickers = self.registry.normalize_rest_tickers(
            rows, self._instruments, received_at=received_at
        )
        if not tickers:
            raise RuntimeError("ticker recovery returned no eligible instruments")
        if not self.ticker_book.apply(tickers, received_at=received_at):
            raise RuntimeError("ticker recovery did not advance the projection")

    async def create_breadth_snapshot(self, *, as_of: datetime | None = None) -> dict[str, Any]:
        as_of = as_of or datetime.now(timezone.utc)
        while self._recovery_times and (as_of - self._recovery_times[0]).total_seconds() > 3600:
            self._recovery_times.popleft()
        missing = () if self._provider_state in {"LIVE", "BOOTSTRAPPED"} else (self.provider.name,)
        breadth = calculate_universe_breadth(
            self.ticker_book.snapshot(),
            eligible_instruments=len(self._instruments),
            as_of=as_of,
            projection_observed_at=self.ticker_book.last_batch_at,
            provider_latency_ms=self._provider_latency_ms,
            missing_providers=missing,
            sequence_gap_recoveries=len(self._recovery_times),
            stale_after_ms=int(self.settings.stale_after_seconds * 1000),
        )
        payload = breadth.as_dict()
        evidence = self.ledger.append(
            event_type="UNIVERSE_BREADTH_SNAPSHOT",
            payload=payload,
            observed_at=as_of,
        )
        self.ledger.set_projection("breadth:global", evidence.evidence_id)
        self._last_snapshot_at = as_of
        payload["evidence_ids"] = [evidence.evidence_id]
        return payload

    async def _snapshot_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.create_breadth_snapshot()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - one snapshot cannot kill collection
                logger.exception("UMIL breadth snapshot failed")
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self.settings.snapshot_interval_seconds
                )
            except asyncio.TimeoutError:
                pass

    async def _metadata_loop(self) -> None:
        retry_delay = 5.0
        while not self._stop_event.is_set():
            delay = self.settings.metadata_refresh_seconds if self._instruments else retry_delay
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=delay
                )
                break
            except asyncio.TimeoutError:
                pass
            try:
                await self.refresh_universe()
                retry_delay = 5.0
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - preserve last valid point-in-time membership
                logger.exception("UMIL universe refresh failed")
                retry_delay = min(300.0, retry_delay * 2.0)

    def latest_breadth(self) -> dict[str, Any] | None:
        evidence = self.ledger.latest("breadth:global")
        if evidence is None:
            return None
        payload = dict(evidence.payload)
        payload["evidence_ids"] = [evidence.evidence_id]
        return payload

    def market_rows(self, *, offset: int = 0, limit: int = 100) -> dict[str, Any]:
        if offset < 0 or not 1 <= limit <= 500:
            raise ValueError("invalid pagination")
        rows = self.ticker_book.snapshot()
        tiers = {row.instrument_id: row for row in assign_tiers(rows)}
        lightweight = calculate_lightweight_states(
            rows, as_of=datetime.now(timezone.utc),
            stale_after_seconds=self.settings.stale_after_seconds,
        )
        items = []
        for row in rows[offset:offset + limit]:
            item = row.as_dict()
            assignment = tiers.get(row.instrument_id)
            item["coverage_tier"] = assignment.tier if assignment else "UNIVERSE"
            intelligence = self.intelligence.latest(row.instrument_id)
            item["intelligence_state"] = (
                intelligence.get("state", "WARMING") if intelligence else "WARMING"
            )
            item["dna"] = intelligence.get("dna") if intelligence else None
            item["universe_state"] = lightweight.get(row.instrument_id)
            instrument = next((value for value in self._instruments if value.instrument_id == row.instrument_id), None)
            scale = self._scale_by_asset.get(instrument.base_asset) if instrument else None
            item["market_scale"] = self._scale_payload(
                instrument.base_asset, scale
            ) if instrument and scale else None
            items.append(item)
        return {
            "api_schema_version": VERSIONS.api_schema_version,
            "as_of": datetime.now(timezone.utc),
            "total": len(rows),
            "offset": offset,
            "limit": limit,
            "items": items,
            "authority": {"read_only": True, "can_influence_ats": False},
        }

    def health(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        last_batch = self.ticker_book.last_batch_at
        age_seconds = (now - last_batch).total_seconds() if last_batch else None
        if not self._instruments:
            state = OperationalState.UNAVAILABLE
        elif age_seconds is None:
            state = OperationalState.WARMING
        elif age_seconds > self.settings.stale_after_seconds:
            state = OperationalState.STALE
        elif self._provider_state not in {"LIVE", "BOOTSTRAPPED"}:
            state = OperationalState.DEGRADED
        else:
            state = OperationalState.READY
        verification = self.ledger.verification_status()
        chain_valid = verification["valid"]
        chain_error = verification["error"]
        if chain_valid is False:
            state = OperationalState.INVALID
        elif chain_valid is None and state == OperationalState.READY:
            state = OperationalState.DEGRADED
        return {
            "service": "umil",
            "service_version": VERSIONS.service_version,
            "state": state.value,
            "provider_state": self._provider_state,
            "provider_reason": self._provider_reason,
            "eligible_instruments": len(self._instruments),
            "observed_instruments": len(self.ticker_book.snapshot()),
            "last_batch_age_seconds": age_seconds,
            "last_snapshot_at": self._last_snapshot_at,
            "sequence_gap_recoveries_last_hour": len(self._recovery_times),
            "sequence_gap_recoveries_since_start": self._sequence_gap_recoveries_total,
            "out_of_order_events": self.ticker_book.out_of_order_events,
            "last_intelligence_at": self._last_intelligence_at,
            "intelligence_failures": len(self._intelligence_failures),
            "scale_state": self._scale_state,
            "scale_reason": self._scale_reason,
            "last_scale_at": self._last_scale_at,
            "evidence_chain_valid": chain_valid,
            "evidence_chain_error": chain_error,
            "evidence_chain_state": verification["state"],
            "evidence_verified_sequence": verification["verified_sequence"],
            "evidence_verified_at": verification["checked_at"],
            "started_at": self._started_at,
        }

    def _resolve_instrument(self, symbol_or_id: str) -> Instrument:
        token = symbol_or_id.strip()
        for instrument in self._instruments:
            if token == instrument.instrument_id or token.upper() == instrument.venue_symbol:
                return instrument
        raise KeyError("instrument not found")

    async def refresh_symbol_intelligence(self, symbol_or_id: str) -> dict[str, Any]:
        instrument = self._resolve_instrument(symbol_or_id)
        as_of = datetime.now(timezone.utc)
        candle_sets: dict[str, Any] = {}
        for interval in self.settings.intelligence_intervals:
            raw = await asyncio.to_thread(
                self.provider.fetch_klines,
                instrument.venue_symbol,
                interval,
                limit=self.settings.candle_limit,
            )
            candle_sets[interval] = normalize_binance_klines(
                instrument.instrument_id, interval, raw, as_of=as_of
            )
        trades = ()
        book = None
        derivatives = None
        tiers = {row.instrument_id: row.tier for row in assign_tiers(self.ticker_book.snapshot())}
        if tiers.get(instrument.instrument_id) == "FOCUS":
            if hasattr(self.provider, "fetch_aggregate_trades"):
                raw_trades = await asyncio.to_thread(
                    self.provider.fetch_aggregate_trades, instrument.venue_symbol, limit=1000
                )
                trades = normalize_binance_trades(instrument.instrument_id, raw_trades)
            if hasattr(self.provider, "fetch_depth"):
                raw_book = await asyncio.to_thread(
                    self.provider.fetch_depth, instrument.venue_symbol, limit=100
                )
                book = normalize_binance_depth(
                    instrument.instrument_id, raw_book, observed_at=as_of
                )
            if instrument.quote_asset == "USDT" and hasattr(self.derivatives_provider, "fetch_context"):
                try:
                    raw_derivatives = await asyncio.to_thread(
                        self.derivatives_provider.fetch_context, instrument.venue_symbol
                    )
                    observation = normalize_binance_futures_context(
                        instrument.venue_symbol, raw_derivatives,
                        previous_open_interest=self._previous_open_interest.get(instrument.venue_symbol),
                    )
                    self._previous_open_interest[instrument.venue_symbol] = observation.open_interest
                    derivatives = calculate_derivatives_metrics(observation)
                except Exception as exc:  # noqa: BLE001 - spot intelligence remains usable
                    logger.info("Derivatives context unavailable for %s: %s", instrument.venue_symbol, type(exc).__name__)
        result = self.intelligence.analyze(
            instrument_id=instrument.instrument_id,
            candles_by_interval=candle_sets,
            as_of=as_of,
            trades=trades,
            order_book=book,
            derivatives=derivatives,
        )
        self._last_intelligence_at = as_of
        self._intelligence_failures.pop(instrument.instrument_id, None)
        return result

    async def refresh_intelligence_batch(self) -> int:
        rows = self.ticker_book.snapshot()
        if not rows:
            return 0
        assignments = assign_tiers(rows)
        ordered = sorted(assignments, key=lambda row: (row.tier != "FOCUS", -row.priority))
        count = min(self.settings.intelligence_batch_size, len(ordered))
        selected = [ordered[(self._intelligence_cursor + index) % len(ordered)] for index in range(count)]
        self._intelligence_cursor = (self._intelligence_cursor + count) % len(ordered)
        completed = 0
        for assignment in selected:
            try:
                await self.refresh_symbol_intelligence(assignment.instrument_id)
                completed += 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - isolate one instrument/provider error
                self._intelligence_failures[assignment.instrument_id] = type(exc).__name__
                logger.exception("UMIL intelligence refresh failed for %s", assignment.instrument_id)
        return completed

    async def _intelligence_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.refresh_intelligence_batch()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - background isolation boundary
                logger.exception("UMIL intelligence batch failed")
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self.settings.intelligence_refresh_seconds
                )
            except asyncio.TimeoutError:
                pass

    def latest_intelligence(self, symbol_or_id: str) -> dict[str, Any] | None:
        instrument = self._resolve_instrument(symbol_or_id)
        return self.intelligence.latest(instrument.instrument_id)

    def market_anomalies(self, *, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.ticker_book.snapshot()
        lightweight = calculate_lightweight_states(
            rows, as_of=datetime.now(timezone.utc),
            stale_after_seconds=self.settings.stale_after_seconds,
        )
        rich = {
            row["instrument_id"]: row for row in self.intelligence.anomalies(limit=limit * 5)
        }
        output = []
        for row in rows:
            quick = lightweight[row.instrument_id]
            detailed = rich.get(row.instrument_id)
            calibration = calibrate_anomaly(quick, detailed)
            output.append({
                "instrument_id": row.instrument_id,
                "as_of": quick["as_of"],
                **calibration,
                "coverage": "FULL_DNA" if detailed else "UNIVERSE",
                "universe_state": quick,
                "dna": detailed.get("dna") if detailed else None,
                "evidence_record_id": detailed.get("evidence_record_id") if detailed else None,
            })
        return sorted(
            output,
            key=lambda item: (
                float(item["anomaly_score"]),
                float(item["confidence_score"]),
                float(item["raw_anomaly_score"]),
            ),
            reverse=True,
        )[:limit]

    async def refresh_scale(self) -> int:
        rows = await asyncio.to_thread(
            self.scale_provider.fetch_markets, pages=self.settings.scale_pages
        )
        observed_at = datetime.now(timezone.utc)
        normalized, ambiguous = normalize_market_scale(rows, observed_at=observed_at)
        payload = {
            "as_of": observed_at,
            "provider": self.scale_provider.name,
            "asset_count": len(normalized),
            "ambiguous_symbols": sorted(ambiguous),
            "assets": {
                symbol: self._scale_payload(symbol, value)
                for symbol, value in normalized.items()
            },
        }
        evidence = self.ledger.append(
            event_type="ASSET_SCALE_SNAPSHOT", payload=payload, observed_at=observed_at
        )
        self.ledger.set_projection("scale:global", evidence.evidence_id)
        self._scale_by_asset = normalized
        self._ambiguous_scale_assets = ambiguous
        self._scale_state = "READY"
        self._scale_reason = (
            "AMBIGUOUS_PROVIDER_SYMBOLS_QUARANTINED"
            if ambiguous
            else None
        )
        self._last_scale_at = observed_at
        return len(normalized)

    async def _scale_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.refresh_scale()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - optional provider isolation
                self._scale_state = "DEGRADED"
                self._scale_reason = type(exc).__name__
                logger.exception("UMIL scale refresh failed")
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self.settings.scale_refresh_seconds
                )
            except asyncio.TimeoutError:
                pass

    def market_scale(self, symbol_or_id: str) -> dict[str, Any] | None:
        instrument = self._resolve_instrument(symbol_or_id)
        value = self._scale_by_asset.get(instrument.base_asset)
        return self._scale_payload(instrument.base_asset, value) if value else None

    def scale_leaders(self, *, limit: int = 100) -> list[dict[str, Any]]:
        rows = [
            self._scale_payload(symbol, value)
            for symbol, value in self._scale_by_asset.items()
        ]
        rows.sort(key=lambda row: row.get("market_cap_usd") or 0, reverse=True)
        return rows[:limit]

    def _scale_payload(self, symbol: str, value: Any) -> dict[str, Any]:
        return {
            "symbol": symbol,
            "asset_id": value.asset_id,
            "as_of": value.observed_at,
            "market_cap_usd": value.market_cap_usd,
            "fully_diluted_value_usd": value.fully_diluted_value_usd,
            "circulating_supply": value.circulating_supply,
            "rank": value.rank,
            "sector": value.sector,
            "identity_quality": "AMBIGUOUS_SYMBOL" if symbol in self._ambiguous_scale_assets else "RESOLVED",
        }
