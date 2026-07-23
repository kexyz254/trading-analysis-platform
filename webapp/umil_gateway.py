"""Strict read-only projection of UMIL's sanctioned public API into ATS.

UMIL remains an isolated local process.  ATS validates every response, preserves
all documented display fields, rejects authority escalation, and exposes no
write, execution, training, risk, credential, or tournament capability.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from fastapi import APIRouter, HTTPException, Query


router = APIRouter(prefix="/api/pro/umil", tags=["UMIL display gateway"])

_MAX_RESPONSE_BYTES = 4_000_000
_TIMEOUT_SECONDS = 4.0
_MAX_FALLBACK_AGE_SECONDS = 300.0
_MAX_JSON_DEPTH = 16
_MAX_JSON_ITEMS = 2_000
_MAX_JSON_KEYS = 256
_MAX_STRING_LENGTH = 20_000
_SYMBOL = re.compile(r"^[A-Z0-9_-]{2,40}$")
_ASSET = re.compile(r"^[A-Z0-9]{2,16}$")
_DNA_KEYS = frozenset({"TRN", "MOM", "PRS", "STR", "VOL", "LIQ", "ACT", "RSK"})
_SCHEMA = "umil-api-v2"


@dataclass(frozen=True)
class _Cached:
    data: Any
    stored_at: datetime


class _UpstreamHTTPError(RuntimeError):
    def __init__(self, status_code: int, detail: Any) -> None:
        super().__init__(f"UMIL upstream returned HTTP {status_code}")
        self.status_code = status_code
        self.detail = detail


_cache: dict[str, _Cached] = {}
_cache_lock = threading.Lock()


def _utc_now() -> datetime:
    """Return UTC time through a deterministic, patchable clock boundary."""
    return datetime.now(timezone.utc)


def _base_url() -> str:
    candidate = os.getenv("ATS_UMIL_BASE_URL", "http://127.0.0.1:8100").rstrip("/")
    parsed = urllib.parse.urlparse(candidate)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("UMIL gateway accepts loopback HTTP only")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("invalid UMIL base URL")
    if parsed.port not in {8100, None}:
        raise ValueError("UMIL gateway port must be 8100")
    return candidate


def _decode_json(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("UMIL returned invalid UTF-8 JSON") from exc


def _read_json(path: str, *, accept_error_detail: bool = False) -> Any:
    request = urllib.request.Request(
        _base_url() + path,
        headers={"Accept": "application/json", "User-Agent": "ATS-UMIL-Display-Gateway/1.6"},
        method="GET",
    )
    try:
        response = urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS)
    except urllib.error.HTTPError as exc:
        raw = exc.read(_MAX_RESPONSE_BYTES + 1)
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise ValueError("UMIL error response exceeds display gateway limit") from exc
        payload = _decode_json(raw) if raw else None
        detail = payload.get("detail") if isinstance(payload, Mapping) else payload
        if accept_error_detail and exc.code == 503 and isinstance(detail, Mapping):
            return dict(detail)
        raise _UpstreamHTTPError(exc.code, detail) from exc

    with response:
        raw = response.read(_MAX_RESPONSE_BYTES + 1)
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise ValueError("UMIL response exceeds display gateway limit")
    return _decode_json(raw)


def _authority(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("missing authority")
    if value.get("read_only") is not True or value.get("can_influence_ats") is not False:
        raise ValueError("UMIL authority escalation")
    forbidden = (
        "can_trade", "can_cancel_orders", "can_access_trading_credentials",
        "can_train", "can_modify_risk", "can_modify_tournament", "can_write_ats_storage",
    )
    if any(value.get(field) is True for field in forbidden):
        raise ValueError("UMIL authority escalation")
    return _json_tree(value)


def _number(value: Any, *, lower: float | None = None, upper: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("invalid numeric display value")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("non-finite display value")
    if lower is not None and result < lower:
        raise ValueError("display value below range")
    if upper is not None and result > upper:
        raise ValueError("display value above range")
    return result


def _optional_number(value: Any, *, lower: float | None = None, upper: float | None = None) -> float | None:
    if value is None:
        return None
    return _number(value, lower=lower, upper=upper)


def _timestamp(value: Any) -> tuple[str, float]:
    if not isinstance(value, str) or not value:
        raise ValueError("missing display timestamp")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        observed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("invalid display timestamp") from exc
    if observed.tzinfo is None:
        raise ValueError("naive display timestamp")
    age = (_utc_now() - observed.astimezone(timezone.utc)).total_seconds()
    if age < -5.0:
        raise ValueError("future-dated display data")
    return value, max(0.0, age)


def _freshness(age_seconds: float) -> str:
    if age_seconds <= 30:
        return "LIVE"
    if age_seconds <= 300:
        return "AGING"
    return "STALE"


def _json_tree(value: Any, *, depth: int = 0) -> Any:
    if depth > _MAX_JSON_DEPTH:
        raise ValueError("UMIL display payload is too deeply nested")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite JSON value")
        return value
    if isinstance(value, str):
        if len(value) > _MAX_STRING_LENGTH:
            raise ValueError("UMIL display string exceeds limit")
        return value
    if isinstance(value, Mapping):
        if len(value) > _MAX_JSON_KEYS:
            raise ValueError("UMIL display object exceeds key limit")
        safe: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 120:
                raise ValueError("invalid UMIL display key")
            safe[key] = _json_tree(item, depth=depth + 1)
        return safe
    if isinstance(value, (list, tuple)):
        if len(value) > _MAX_JSON_ITEMS:
            raise ValueError("UMIL display list exceeds item limit")
        return [_json_tree(item, depth=depth + 1) for item in value]
    raise ValueError("unsupported UMIL display value")


def _mapping(payload: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError(f"invalid {label} payload")
    return payload


def _schema(payload: Mapping[str, Any], label: str) -> None:
    if payload.get("api_schema_version") != _SCHEMA:
        raise ValueError(f"incompatible {label} schema")


def _instrument(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 100 or value.count(":") < 2:
        raise ValueError("invalid UMIL instrument identity")
    symbol = value.rsplit(":", 1)[-1]
    if not _SYMBOL.fullmatch(symbol):
        raise ValueError("invalid UMIL instrument symbol")
    return value


def _dna(value: Any) -> dict[str, float] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != _DNA_KEYS:
        raise ValueError("invalid Market DNA")
    return {key: _number(value[key], lower=0, upper=100) for key in sorted(_DNA_KEYS)}


def _health_payload(payload: Any) -> dict[str, Any]:
    item = _mapping(payload, "health")
    if item.get("service") != "umil" or not isinstance(item.get("state"), str):
        raise ValueError("invalid UMIL health contract")
    if not isinstance(item.get("service_version"), str):
        raise ValueError("missing UMIL service version")
    for field in ("last_snapshot_at", "last_intelligence_at", "last_scale_at", "started_at"):
        if item.get(field) is not None:
            _timestamp(item[field])
    return _json_tree(item)


def _authority_payload(payload: Any) -> dict[str, Any]:
    item = _mapping(payload, "authority")
    _authority(item.get("authority"))
    if not isinstance(item.get("versions"), Mapping):
        raise ValueError("missing UMIL versions")
    return _json_tree(item)


def _universe(payload: Any) -> dict[str, Any]:
    item = _mapping(payload, "universe")
    _schema(item, "universe")
    _authority(item.get("authority"))
    rows = item.get("items")
    if not isinstance(rows, list) or len(rows) > 500:
        raise ValueError("invalid universe rows")
    for row in rows:
        row = _mapping(row, "universe row")
        _instrument(row.get("instrument_id"))
        symbol = row.get("venue_symbol")
        if not isinstance(symbol, str) or not _SYMBOL.fullmatch(symbol):
            raise ValueError("invalid universe symbol")
        for field in ("first_seen_at", "last_seen_at"):
            if row.get(field) is not None:
                _timestamp(row[field])
    return _json_tree(item)


def _breadth(payload: Any) -> dict[str, Any]:
    item = _mapping(payload, "breadth")
    _schema(item, "breadth")
    _authority(item.get("authority"))
    _timestamp(item.get("as_of"))
    counts = []
    for field in ("eligible_instruments", "observed_instruments", "advancers", "decliners", "unchanged"):
        value = item.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("invalid breadth count")
        counts.append(value)
    if counts[1] != counts[2] + counts[3] + counts[4]:
        raise ValueError("breadth counts do not reconcile")
    _number(item.get("advance_ratio"), lower=0, upper=1)
    _number(item.get("volume_weighted_advance_ratio"), lower=0, upper=1)
    return _json_tree(item)


def _market_rows(payload: Any) -> dict[str, Any]:
    item = _mapping(payload, "market list")
    _schema(item, "market-list")
    _authority(item.get("authority"))
    _timestamp(item.get("as_of"))
    rows = item.get("items")
    if not isinstance(rows, list) or len(rows) > 500:
        raise ValueError("invalid market-list rows")
    total = item.get("total")
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise ValueError("invalid market-list total")
    for row in rows:
        row = _mapping(row, "market row")
        _instrument(row.get("instrument_id"))
        symbol = row.get("venue_symbol")
        if not isinstance(symbol, str) or not _SYMBOL.fullmatch(symbol):
            raise ValueError("invalid venue symbol")
        _number(row.get("last_price"), lower=0)
        _number(row.get("return_24h_pct"))
        _number(row.get("quote_volume_24h"), lower=0)
        _timestamp(row.get("event_time"))
        _dna(row.get("dna"))
        if not isinstance(row.get("universe_state"), Mapping):
            raise ValueError("missing universe state")
    return _json_tree(item)


def _anomalies(payload: Any) -> dict[str, Any]:
    item = _mapping(payload, "anomaly")
    _schema(item, "anomaly")
    authority = _authority(item.get("authority"))
    rows = item.get("items")
    if not isinstance(rows, list) or len(rows) > 100:
        raise ValueError("invalid anomaly rows")

    safe_rows: list[dict[str, Any]] = []
    rejected = 0
    for candidate in rows:
        try:
            row = _mapping(candidate, "anomaly row")
            instrument = _instrument(row.get("instrument_id"))
            _, age = _timestamp(row.get("as_of"))
            _number(row.get("anomaly_score"), lower=0, upper=100)
            _dna(row.get("dna"))
            state = _mapping(row.get("universe_state"), "anomaly universe state")
            _timestamp(state.get("as_of"))
            copied = _json_tree(row)
            copied["symbol"] = instrument.rsplit(":", 1)[-1]
            copied["age_seconds"] = round(age, 3)
            copied["display_freshness"] = _freshness(age)
            safe_rows.append(copied)
        except ValueError:
            rejected += 1

    if rows and not safe_rows:
        raise ValueError("all anomaly rows were invalid")
    return {
        "api_schema_version": _SCHEMA,
        "items": safe_rows,
        "gateway_rejected_rows": rejected,
        "authority": authority,
    }


def _intelligence(payload: Any) -> dict[str, Any]:
    item = _mapping(payload, "intelligence")
    _instrument(item.get("instrument_id"))
    _timestamp(item.get("as_of"))
    _authority(item.get("authority"))
    _dna(item.get("dna"))
    if not isinstance(item.get("dimensions"), Mapping):
        raise ValueError("missing intelligence dimensions")
    if not isinstance(item.get("timeframes"), Mapping):
        raise ValueError("missing intelligence timeframes")
    if not isinstance(item.get("versions"), Mapping):
        raise ValueError("missing intelligence versions")
    return _json_tree(item)


def _dna_payload(payload: Any) -> dict[str, Any]:
    item = _mapping(payload, "DNA")
    _instrument(item.get("instrument_id"))
    _timestamp(item.get("as_of"))
    _authority(item.get("authority"))
    _dna(item.get("dna"))
    if not isinstance(item.get("dimensions"), Mapping):
        raise ValueError("missing DNA dimensions")
    return _json_tree(item)


def _insights(payload: Any) -> dict[str, Any]:
    item = _mapping(payload, "insights")
    _instrument(item.get("instrument_id"))
    _timestamp(item.get("as_of"))
    _authority(item.get("authority"))
    commentary = _mapping(item.get("commentary"), "commentary")
    if not isinstance(commentary.get("summary"), str) or not isinstance(commentary.get("claims"), list):
        raise ValueError("invalid commentary")
    return _json_tree(item)


def _scale_item(value: Any) -> Mapping[str, Any]:
    item = _mapping(value, "scale row")
    symbol = item.get("symbol")
    if not isinstance(symbol, str) or not symbol or len(symbol) > 80:
        raise ValueError("invalid scale symbol")
    _timestamp(item.get("as_of"))
    _optional_number(item.get("market_cap_usd"), lower=0)
    _optional_number(item.get("fully_diluted_value_usd"), lower=0)
    _optional_number(item.get("circulating_supply"), lower=0)
    rank = item.get("rank")
    if rank is not None and (isinstance(rank, bool) or not isinstance(rank, int) or rank < 1):
        raise ValueError("invalid scale rank")
    return item


def _scale_list(payload: Any) -> dict[str, Any]:
    item = _mapping(payload, "scale list")
    _schema(item, "scale")
    _authority(item.get("authority"))
    rows = item.get("items")
    if not isinstance(rows, list) or len(rows) > 500:
        raise ValueError("invalid scale list")
    for row in rows:
        _scale_item(row)
    return _json_tree(item)


def _scale(payload: Any) -> dict[str, Any]:
    item = _mapping(payload, "scale")
    _authority(item.get("authority"))
    _scale_item(item.get("scale"))
    return _json_tree(item)


def _history(payload: Any) -> dict[str, Any]:
    item = _mapping(payload, "history")
    _timestamp(item.get("requested_as_of"))
    _authority(item.get("authority"))
    evidence = _mapping(item.get("evidence"), "historical evidence")
    _instrument(evidence.get("instrument_id"))
    for field in ("observed_at", "received_at", "calculated_at"):
        _timestamp(evidence.get(field))
    return _json_tree(item)


def _evidence(payload: Any) -> dict[str, Any]:
    item = _mapping(payload, "evidence")
    try:
        uuid.UUID(str(item.get("evidence_id")))
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid evidence identity") from exc
    _instrument(item.get("instrument_id"))
    for field in ("observed_at", "received_at", "calculated_at"):
        _timestamp(item.get(field))
    return _json_tree(item)


def _envelope(status: str, data: Any, *, age_seconds: float = 0.0) -> dict[str, Any]:
    return {
        "gateway_status": status,
        "source": "UMIL",
        "served_at": datetime.now(timezone.utc),
        "fallback_age_seconds": round(age_seconds, 3),
        "data": data,
    }


def _validated(
    key: str,
    path: str,
    sanitizer: Callable[[Any], Any],
    *,
    accept_error_detail: bool = False,
    passthrough_statuses: frozenset[int] = frozenset({400, 404, 422}),
) -> dict[str, Any]:
    upstream_error: _UpstreamHTTPError | None = None
    try:
        data = sanitizer(_read_json(path, accept_error_detail=accept_error_detail))
        with _cache_lock:
            _cache[key] = _Cached(data=data, stored_at=datetime.now(timezone.utc))
        return _envelope("LIVE", data)
    except _UpstreamHTTPError as exc:
        upstream_error = exc
    except Exception as exc:
        failure = exc
    else:  # pragma: no cover - defensive only
        failure = RuntimeError("unknown UMIL gateway failure")

    with _cache_lock:
        cached = _cache.get(key)
    if cached is not None:
        age = (datetime.now(timezone.utc) - cached.stored_at).total_seconds()
        if age <= _MAX_FALLBACK_AGE_SECONDS:
            return _envelope("STALE", cached.data, age_seconds=age)

    if upstream_error is not None and upstream_error.status_code in passthrough_statuses:
        detail = _json_tree(upstream_error.detail) if upstream_error.detail is not None else "UMIL request failed"
        raise HTTPException(status_code=upstream_error.status_code, detail=detail)
    cause = upstream_error if upstream_error is not None else failure
    raise HTTPException(status_code=503, detail="UMIL display data is unavailable") from cause


def _normalize_symbol(symbol: str) -> str:
    normalized = symbol.strip().upper()
    if not _SYMBOL.fullmatch(normalized):
        raise HTTPException(status_code=400, detail="invalid market symbol")
    return normalized


@router.get("/health")
async def health() -> dict[str, Any]:
    try:
        envelope = await asyncio.to_thread(
            _validated, "health", "/health/ready", _health_payload,
            accept_error_detail=True,
        )
        state = str(envelope["data"].get("state", "UNAVAILABLE")).upper()
        if envelope["gateway_status"] == "LIVE" and state not in {"READY", "DEGRADED"}:
            envelope["gateway_status"] = state
        return envelope
    except HTTPException:
        return _envelope("UNAVAILABLE", {"state": "UNAVAILABLE", "evidence_chain_valid": False})


@router.get("/authority")
async def authority() -> dict[str, Any]:
    return await asyncio.to_thread(
        _validated, "authority", "/api/v1/authority", _authority_payload,
    )


@router.get("/universe")
async def universe(
    quote_asset: str | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=500),
) -> dict[str, Any]:
    params: dict[str, Any] = {"offset": offset, "limit": limit}
    if quote_asset is not None:
        normalized = quote_asset.strip().upper()
        if not _ASSET.fullmatch(normalized):
            raise HTTPException(status_code=400, detail="invalid quote asset")
        params["quote_asset"] = normalized
    query = urllib.parse.urlencode(params)
    return await asyncio.to_thread(
        _validated, f"universe:{query}", f"/api/v1/market-universe?{query}", _universe,
    )


@router.get("/breadth")
async def breadth() -> dict[str, Any]:
    return await asyncio.to_thread(
        _validated, "breadth", "/api/v1/market-breadth", _breadth,
    )


@router.get("/markets")
async def markets(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=500),
) -> dict[str, Any]:
    query = urllib.parse.urlencode({"offset": offset, "limit": limit})
    return await asyncio.to_thread(
        _validated, f"markets:{query}", f"/api/v1/market-intelligence?{query}", _market_rows,
    )


@router.get("/anomalies")
async def anomalies(limit: int = Query(default=25, ge=1, le=100)) -> dict[str, Any]:
    return await asyncio.to_thread(
        _validated, f"anomalies:{limit}", f"/api/v1/market-anomalies?limit={limit}", _anomalies,
    )


@router.get("/intelligence/{symbol}")
async def intelligence(symbol: str) -> dict[str, Any]:
    normalized = _normalize_symbol(symbol)
    return await asyncio.to_thread(
        _validated, f"intelligence:{normalized}",
        f"/api/v1/market-intelligence/{urllib.parse.quote(normalized)}", _intelligence,
    )


@router.get("/dna/{symbol}")
async def dna(symbol: str) -> dict[str, Any]:
    normalized = _normalize_symbol(symbol)
    return await asyncio.to_thread(
        _validated, f"dna:{normalized}",
        f"/api/v1/market-dna/{urllib.parse.quote(normalized)}", _dna_payload,
    )


@router.get("/insights/{symbol}")
async def insights(symbol: str) -> dict[str, Any]:
    normalized = _normalize_symbol(symbol)
    return await asyncio.to_thread(
        _validated, f"insights:{normalized}",
        f"/api/v1/market-insights/{urllib.parse.quote(normalized)}", _insights,
    )


@router.get("/scale")
async def scale_leaders(limit: int = Query(default=100, ge=1, le=500)) -> dict[str, Any]:
    return await asyncio.to_thread(
        _validated, f"scale:{limit}", f"/api/v1/market-scale?limit={limit}", _scale_list,
    )


@router.get("/scale/{symbol}")
async def scale(symbol: str) -> dict[str, Any]:
    normalized = _normalize_symbol(symbol)
    return await asyncio.to_thread(
        _validated, f"scale-symbol:{normalized}",
        f"/api/v1/market-scale/{urllib.parse.quote(normalized)}", _scale,
    )


@router.get("/history/{symbol}")
async def history(symbol: str, as_of: str = Query(..., min_length=10, max_length=64)) -> dict[str, Any]:
    normalized = _normalize_symbol(symbol)
    try:
        _, _ = _timestamp(as_of)
        parsed = datetime.fromisoformat(as_of[:-1] + "+00:00" if as_of.endswith("Z") else as_of)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="as_of must be a timezone-aware ISO timestamp") from exc
    canonical = parsed.isoformat()
    path = (
        f"/api/v1/historical/intelligence/{urllib.parse.quote(normalized)}?"
        + urllib.parse.urlencode({"as_of": canonical})
    )
    return await asyncio.to_thread(
        _validated, f"history:{normalized}:{canonical}", path, _history,
    )


@router.get("/evidence/{evidence_id}")
async def evidence(evidence_id: str) -> dict[str, Any]:
    try:
        normalized = str(uuid.UUID(evidence_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid evidence identity") from exc
    return await asyncio.to_thread(
        _validated, f"evidence:{normalized}",
        f"/api/v1/evidence/{urllib.parse.quote(normalized)}", _evidence,
    )
