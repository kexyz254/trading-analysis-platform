"""Orchestrates deterministic intelligence while preserving exact lineage."""

from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from .commentary import deterministic_commentary, validate_commentary
from .derivatives import DerivativesMetrics
from .dna import MarketDNASnapshot, build_market_dna, normalize_market_dna
from .evidence import EvidenceLedger
from .features import TimeframeFeatures, calculate_multi_timeframe_features
from .microstructure import (
    BookMetrics,
    FlowMetrics,
    PressureAssessment,
    assess_pressure,
    calculate_book_metrics,
    calculate_trade_flow,
)
from .observations import AggTrade, Candle, DerivativesObservation, OrderBookSnapshot
from .versions import VERSIONS
from .watch_conditions import (
    WatchState,
    condition_from_dict,
    create_structure_watches,
    evaluate_condition,
)


class IntelligenceEngine:
    """Pure calculations plus append-only persistence; no trading authority."""

    def __init__(self, ledger: EvidenceLedger) -> None:
        self.ledger = ledger

    def analyze(
        self,
        *,
        instrument_id: str,
        candles_by_interval: Mapping[str, Iterable[Candle]],
        as_of: datetime | None = None,
        trades: Iterable[AggTrade] = (),
        order_book: OrderBookSnapshot | None = None,
        derivatives: DerivativesMetrics | None = None,
    ) -> dict[str, Any]:
        as_of = as_of or datetime.now(timezone.utc)
        candle_sets = {key: tuple(value) for key, value in candles_by_interval.items()}
        trade_rows = tuple(trades)
        input_record = self.ledger.append(
            event_type="MARKET_INPUT_BATCH",
            instrument_id=instrument_id,
            observed_at=as_of,
            externalize_payload=True,
            payload={
                "instrument_id": instrument_id,
                "as_of": as_of,
                "candles": {
                    key: [asdict(candle) for candle in values]
                    for key, values in candle_sets.items()
                },
                "trades": [asdict(trade) for trade in trade_rows],
                "order_book": asdict(order_book) if order_book else None,
                "input_policy": {
                    "closed_candles_only_for_features": True,
                    "flow_forward_filled": False,
                    "book_forward_filled": False,
                },
            },
        )
        features = calculate_multi_timeframe_features(candle_sets)
        if not features:
            raise ValueError("intelligence requires closed-candle evidence")
        feature_record = self.ledger.append(
            event_type="FEATURE_OBSERVATION",
            instrument_id=instrument_id,
            observed_at=as_of,
            payload={
                "instrument_id": instrument_id,
                "as_of": as_of,
                "features": {key: value.as_dict() for key, value in features.items()},
                "feature_schema_version": VERSIONS.feature_schema_version,
                "calculation_version": VERSIONS.calculation_version,
                "source_evidence_ids": [input_record.evidence_id],
            },
        )
        flow = self._flow(trade_rows, as_of=as_of)
        book = calculate_book_metrics(order_book) if order_book is not None else None
        pressure = assess_pressure(flow, book)
        micro_record = None
        if flow is not None or book is not None:
            micro_record = self.ledger.append(
                event_type="MICROSTRUCTURE_OBSERVATION",
                instrument_id=instrument_id,
                observed_at=as_of,
                payload={
                    "instrument_id": instrument_id,
                    "as_of": as_of,
                    "flow": flow.as_dict() if flow else None,
                    "book": book.as_dict() if book else None,
                    "pressure": pressure.as_dict(),
                    "source_evidence_ids": [input_record.evidence_id],
                },
            )
        evidence_ids = [input_record.evidence_id, feature_record.evidence_id]
        if micro_record:
            evidence_ids.append(micro_record.evidence_id)
        dna = build_market_dna(
            snapshot_id=str(uuid.uuid4()), instrument_id=instrument_id,
            as_of=as_of, timeframes=features, pressure=pressure, book=book,
            derivatives=derivatives, evidence_ids=evidence_ids,
        )
        histories, peers = self._calibration_populations(instrument_id, as_of=as_of)
        dna = normalize_market_dna(dna, histories=histories, peers=peers)
        watches = self._update_watches(
            instrument_id=instrument_id,
            candle_sets=candle_sets,
            features=features,
            as_of=as_of,
            evidence_ids=tuple(evidence_ids),
        )
        commentary = deterministic_commentary(dna)
        allowed_keys = {
            item for component in dna.components.values() for item in component.inputs
        }
        valid, errors = validate_commentary(
            commentary, allowed_evidence_ids=evidence_ids,
            allowed_evidence_keys=allowed_keys,
        )
        if not valid:
            raise ValueError("commentary validation failed: " + ",".join(errors))
        payload = dna.as_dict()
        payload["timeframes"] = {key: value.as_dict() for key, value in features.items()}
        payload["microstructure"] = {
            "flow": flow.as_dict() if flow else None,
            "book": book.as_dict() if book else None,
            "pressure": pressure.as_dict(),
        }
        payload["derivatives"] = derivatives.as_dict() if derivatives else None
        payload["commentary"] = commentary.as_dict()
        payload["watch_conditions"] = watches
        record = self.ledger.append(
            event_type="MARKET_INTELLIGENCE_SNAPSHOT",
            instrument_id=instrument_id,
            observed_at=as_of,
            payload=payload,
        )
        self.ledger.set_projection(f"intelligence:{instrument_id}", record.evidence_id)
        output = dict(record.payload)
        output["evidence_record_id"] = record.evidence_id
        return output

    def _calibration_populations(
        self, instrument_id: str, *, as_of: datetime,
    ) -> tuple[dict[str, list[float]], dict[str, list[float]]]:
        histories: dict[str, list[float]] = {}
        peers: dict[str, list[float]] = {}
        for row in self.ledger.history(
            event_type="MARKET_INTELLIGENCE_SNAPSHOT",
            instrument_id=instrument_id, before=as_of, limit=200,
        ):
            for key, value in row.payload.get("dna", {}).items():
                if isinstance(value, (int, float)):
                    histories.setdefault(key, []).append(float(value))
        for row in self.ledger.latest_per_instrument(
            event_type="MARKET_INTELLIGENCE_SNAPSHOT", as_of=as_of, limit=2000,
        ):
            if row.instrument_id == instrument_id:
                continue
            for key, value in row.payload.get("dna", {}).items():
                if isinstance(value, (int, float)):
                    peers.setdefault(key, []).append(float(value))
        return histories, peers

    def _update_watches(
        self,
        *,
        instrument_id: str,
        candle_sets: Mapping[str, tuple[Candle, ...]],
        features: Mapping[str, TimeframeFeatures],
        as_of: datetime,
        evidence_ids: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        preferred = next((key for key in ("5m", "15m", "1h", "4h", "1d") if key in features), None)
        if preferred is None:
            return []
        closed = tuple(row for row in candle_sets[preferred] if row.closed)
        if len(closed) < 20:
            return []
        current_price = closed[-1].close
        previous = self.ledger.latest(f"watch:{instrument_id}")
        conditions = []
        if previous is not None:
            try:
                conditions = [
                    condition_from_dict(item)
                    for item in previous.payload.get("conditions", [])
                ]
            except (KeyError, TypeError, ValueError):
                conditions = []
        if conditions:
            evaluated = [
                evaluate_condition(item, {"last_price": current_price}, as_of=as_of)
                for item in conditions
            ]
            changed = any(before.state is not after.state for before, after in zip(conditions, evaluated))
            conditions = evaluated
            event = "WATCH_CONDITION_TRANSITION" if changed else None
        else:
            recent = closed[-20:]
            conditions = list(create_structure_watches(
                instrument_id, features[preferred], current_price=current_price,
                range_high=max(row.high for row in recent),
                range_low=min(row.low for row in recent), created_at=as_of,
                evidence_ids=evidence_ids,
            ))
            event = "WATCH_CONDITION_CREATED"
        payload = {
            "instrument_id": instrument_id, "as_of": as_of,
            "conditions": [item.as_dict() for item in conditions],
            "active_count": sum(item.state is WatchState.ACTIVE for item in conditions),
            "source_evidence_ids": list(evidence_ids),
        }
        if event is not None:
            record = self.ledger.append(
                event_type=event, instrument_id=instrument_id,
                observed_at=as_of, payload=payload,
            )
            self.ledger.set_projection(f"watch:{instrument_id}", record.evidence_id)
            payload["evidence_record_id"] = record.evidence_id
        elif previous is not None:
            payload["evidence_record_id"] = previous.evidence_id
        return payload["conditions"]

    @staticmethod
    def _flow(trades: tuple[AggTrade, ...], *, as_of: datetime) -> FlowMetrics | None:
        return calculate_trade_flow(trades, as_of=as_of) if trades else None

    def latest(self, instrument_id: str) -> dict[str, Any] | None:
        row = self.ledger.latest(f"intelligence:{instrument_id}")
        if row is None:
            return None
        payload = dict(row.payload)
        payload["evidence_record_id"] = row.evidence_id
        return payload

    def anomalies(self, *, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.ledger.latest_per_instrument(
            event_type="MARKET_INTELLIGENCE_SNAPSHOT", limit=max(limit * 5, 100)
        )
        output = []
        for row in rows:
            payload = row.payload
            dna = payload.get("dna", {})
            anomaly = max(
                abs(float(dna.get("VOL", 50)) - 50),
                abs(float(dna.get("ACT", 50)) - 50),
                abs(float(dna.get("PRS", 50)) - 50),
                float(dna.get("RSK", 0)) / 2,
            )
            output.append({
                "instrument_id": row.instrument_id,
                "as_of": payload.get("as_of"),
                "anomaly_score": round(anomaly * 2, 2),
                "dna": dna,
                "state": payload.get("state"),
                "evidence_record_id": row.evidence_id,
            })
        return sorted(output, key=lambda item: item["anomaly_score"], reverse=True)[:limit]
