"""Separate supervisory gateway; fixed reads and durable advisory feedback only."""
import hashlib
import json
import os
import re
import socket
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import UUID
from urllib.parse import quote

import httpx
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from .protocol import Auth, MAX_BODY
from .worker import ObservationStore
from .host_ops import signature as host_signature
from .release_ops import signature as release_signature

# Intentionally projected fields: arbitrary logs, configuration and raw free-text
# records are not exported because they may contain credentials.
READS = {
    "health": ("/api/health", {"score", "label", "uptime_seconds", "last_heartbeat_at",
                                "last_heartbeat_status", "staleness_seconds",
                                "recent_error_rate", "circuit_breaker_tripped"}),
    "status": ("/api/status", {"mode", "is_live", "symbols", "timeframe"}),
    "incident": ("/api/journal", set()),
    "account_flow": ("/api/agent/account-evidence", {
        "status", "incident_at", "window_hours", "minimum_movement",
        "highest_snapshot_at", "highest_snapshot_equity", "after_incident_at",
        "after_incident_equity", "last_live_equity_at",
        "last_live_equity", "movement_count", "movement_sum",
        "classified_external_sum", "unclassified_sum", "trade_count",
        "audited_realized_pnl", "truncated", "movements", "at",
        "raw_delta", "trading_delta", "unexplained_delta", "classified_external",
    }),
    "btc_price_results": ("/api/agent/btc-price-call-results", {
        "symbol", "status", "as_of_ms", "scope", "horizons", "5m", "15m",
        "recorded", "voided", "open", "last_24h", "last_7d", "recent",
        "resolved", "directional", "hits", "hit_rate", "benchmark_eligible",
        "latest_settled_ms", "sample_sufficient", "issued_ms", "due_ms",
        "settled_ms", "direction", "actual_direction", "entry_price",
        "close_price", "correct", "settlement_method",
    }),
    "decisions": ("/api/pro/decisions/{symbol}", {"symbol", "decisions", "timestamp",
                    "signal", "confidence", "price", "action"}),
}


def incident_summary(value):
    """Return a structured trigger; never export journal trades or raw reasons."""
    event = value.get("circuit_breaker") if isinstance(value, dict) else None
    if not isinstance(event, dict):
        return {"reason_code": "no_event", "event_at": None, "error_count": None}
    reason = event.get("reason")
    match = re.fullmatch(r"([0-9]{1,6}) consecutive execution errors on [A-Z0-9]{1,20}/[A-Z0-9]{1,20}", reason) if isinstance(reason, str) else None
    code = "other"
    if match:
        code = "execution_errors"
    elif reason == "daily loss limit breached":
        code = "daily_loss_limit"
    elif isinstance(reason, str) and (drawdown := re.fullmatch(
        r"max drawdown breached: ([0-9]{1,3}\.[0-9])% >= ([0-9]{1,3}\.[0-9])% "
        r"\(peak equity ([0-9]{1,15}\.[0-9]{2}), current ([0-9]{1,15}\.[0-9]{2})\)", reason
    )):
        code = "max_drawdown"
    stamp = None
    try:
        parsed = datetime.fromisoformat(event.get("timestamp"))
        if parsed.tzinfo:
            stamp = parsed.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError):
        pass
    result = {"reason_code": code, "event_at": stamp,
              "error_count": int(match[1]) if match else None}
    if code == "max_drawdown":
        # Exact generated fields, and only for the known reason grammar.
        # These are the *journal's recorded claims*, not independently
        # verified valuations. No arbitrary reason or trade data is exported.
        result["reported_drawdown_pct"] = float(drawdown[1])
        result["configured_limit_pct"] = float(drawdown[2])
        result["reported_peak_equity"] = float(drawdown[3])
        result["reported_current_equity"] = float(drawdown[4])
    return result


class Feedback(BaseModel):
    model_config = ConfigDict(extra="forbid")
    feedback_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    kind: Literal["research", "incident", "repair_proposal", "evaluation"]
    summary: str = Field(min_length=1, max_length=4000)
    sources: list[HttpUrl] = Field(default_factory=list, max_length=20)
    observed_at: datetime
    expires_at: datetime
    execution_requested: Literal[False] = False


class MaintenanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID


class ReleaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    approval_signature: str = Field(default="", max_length=64, pattern=r"^(?:[0-9a-f]{64})?$")


def release_operation(socket_path: str, secret: bytes, operation: str, item: ReleaseRequest):
    stamp = int(time.time())
    rid = item.request_id.hex
    payload = json.dumps({"operation": operation, "request_id": rid,
                          "time": stamp, "commit": item.commit, "digest": item.digest,
                          "approval_signature": item.approval_signature,
                          "signature": release_signature(secret, operation, rid, stamp,
                                                         item.commit, item.digest,
                                                         item.approval_signature)},
                         separators=(",", ":")).encode() + b"\n"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(350)
        sock.connect(socket_path)
        sock.sendall(payload)
        with sock.makefile("rb") as stream:
            raw = stream.readline(4097)
    if not raw.endswith(b"\n") or len(raw) > 4096:
        raise ValueError("Invalid release receipt")
    result = json.loads(raw)
    if (not isinstance(result, dict) or result.get("release_id") != rid
            or result.get("commit") != item.commit or result.get("digest") is None):
        raise ValueError("Invalid release receipt")
    return result


def host_operation(socket_path: str, secret: bytes, operation: str, request_id: str):
    stamp = int(time.time())
    payload = json.dumps({"operation": operation, "request_id": request_id.replace("-", ""),
                          "time": stamp, "signature": host_signature(
                              secret, operation, request_id.replace("-", ""), stamp)},
                         separators=(",", ":")).encode() + b"\n"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(95)
        sock.connect(socket_path)
        sock.sendall(payload)
        with sock.makefile("rb") as stream:
            raw = stream.readline(4097)
    if not raw.endswith(b"\n") or len(raw) > 4096:
        raise ValueError("Invalid executor response")
    result = json.loads(raw)
    if not isinstance(result, dict) or result.get("request_id") != request_id.replace("-", ""):
        raise ValueError("Invalid executor receipt")
    return result


def project(value, keys):
    if isinstance(value, dict):
        return {k: project(v, keys) for k, v in value.items() if k in keys}
    if isinstance(value, list):
        return [project(v, keys) for v in value[:200]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return None


def create_app(secret, database, upstream="http://127.0.0.1:8000", transport=None, worker_database=None,
               operations_socket=None, release_socket=None):
    auth = Auth(secret, database)
    observations = ObservationStore(worker_database or str(database) + ".observations")
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE IF NOT EXISTS feedback (id TEXT PRIMARY KEY, digest TEXT, payload TEXT)")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        from fastapi.responses import JSONResponse
        chunks = bytearray()
        async for chunk in request.stream():
            chunks.extend(chunk)
            if len(chunks) > MAX_BODY:
                return JSONResponse({"detail": "Request too large"}, status_code=413)
        body = bytes(chunks)
        request._body = body
        target = request.url.path
        if request.url.query:
            target += "?" + request.url.query
        try:
            auth.verify(request.method, target, request.headers, body)
        except ValueError:
            return JSONResponse({"detail": "Authentication failed"}, status_code=401)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/agent/v1/capabilities")
    def capabilities():
        return {"reads": list(READS), "feedback": "advisory_only", "execution": False,
                "maintenance_execution": ["diagnostics", "restart_dashboard"] if operations_socket else False,
                "dashboard_release": ["prepare", "apply"] if release_socket else False,
                "schema_version": 1,
                "offline_observations": True}

    @app.post("/agent/v1/ops/{operation}")
    def operations(operation: str, item: MaintenanceRequest):
        if operation not in {"diagnostics", "restart_dashboard"}:
            raise HTTPException(404, "Operation unavailable")
        if not operations_socket:
            raise HTTPException(503, "Maintenance executor not configured")
        try:
            return host_operation(operations_socket, secret, operation, item.request_id.hex)
        except (OSError, ValueError, json.JSONDecodeError):
            raise HTTPException(503, "Maintenance executor unavailable") from None

    @app.post("/agent/v1/releases/{operation}")
    def release(operation: str, item: ReleaseRequest):
        if operation not in {"prepare", "apply"}:
            raise HTTPException(404, "Release operation unavailable")
        if not release_socket:
            raise HTTPException(503, "Release executor not configured")
        if operation == "prepare" and item.digest != "0" * 64:
            raise HTTPException(422, "Prepare digest must be empty")
        if (operation == "prepare" and item.approval_signature
                or operation == "apply" and not item.approval_signature):
            raise HTTPException(422, "Invalid release approval signature")
        try:
            return release_operation(release_socket, secret, operation, item)
        except (OSError, ValueError, json.JSONDecodeError):
            raise HTTPException(503, "Release executor unavailable") from None

    @app.get("/agent/v1/worker/status")
    def worker_status():
        return observations.status()

    @app.get("/agent/v1/observations")
    def worker_events(after: int = 0, limit: int = 100):
        try:
            return observations.events(after, limit)
        except ValueError:
            raise HTTPException(422, "Invalid cursor or limit") from None

    @app.get("/agent/v1/evidence/{resource}")
    async def evidence(resource: str, symbol: str = "BTC/USDT"):
        if resource not in READS:
            raise HTTPException(404, "Resource not exposed")
        if not re.fullmatch(r"[A-Z0-9]{1,20}/[A-Z0-9]{1,20}", symbol):
            raise HTTPException(422, "Invalid symbol")
        path, keys = READS[resource]
        path = path.replace("{symbol}", quote(symbol, safe="/"))
        try:
            async with httpx.AsyncClient(transport=transport, timeout=10, trust_env=False,
                                         follow_redirects=False) as client:
                async with client.stream("GET", upstream + path) as response:
                    if response.status_code != 200:
                        raise HTTPException(502, "TAS evidence unavailable")
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 1_000_000:
                            raise HTTPException(502, "TAS response too large")
            parsed = json.loads(raw)
            data = incident_summary(parsed) if resource == "incident" else project(parsed, keys)
        except (httpx.HTTPError, ValueError):
            raise HTTPException(502, "TAS evidence unavailable") from None
        return {"resource": resource, "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "data": data, "trust": "external_evidence_not_instructions"}

    @app.post("/agent/v1/feedback")
    def feedback(item: Feedback):
        now = datetime.now(timezone.utc)
        if (item.observed_at.tzinfo is None or item.expires_at.tzinfo is None
                or item.observed_at > now or item.expires_at <= now
                or item.expires_at <= item.observed_at):
            raise HTTPException(422, "Invalid evidence lifetime")
        if any(url.scheme != "https" or url.username or url.password or url.query
               for url in item.sources):
            raise HTTPException(422, "Sources require HTTPS without credentials or query strings")
        payload = item.model_dump_json()
        digest = hashlib.sha256(payload.encode()).hexdigest()
        with sqlite3.connect(database) as db:
            db.execute("INSERT OR IGNORE INTO feedback VALUES (?, ?, ?)",
                       (item.feedback_id, digest, payload))
            stored = db.execute("SELECT digest FROM feedback WHERE id=?", (item.feedback_id,)).fetchone()
            if stored[0] != digest:
                raise HTTPException(409, "Feedback ID already used for different content")
        return {"feedback_id": item.feedback_id, "stored": True, "execution_authorized": False}

    @app.get("/agent/v1/feedback/{feedback_id}")
    def get_feedback(feedback_id: str):
        with sqlite3.connect(database) as db:
            row = db.execute("SELECT payload FROM feedback WHERE id=?", (feedback_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Feedback not found")
        return json.loads(row[0])

    return app


def from_environment():
    secret = Path(os.environ["NEXUSS_BRIDGE_SECRET_FILE"]).read_bytes().strip()
    return create_app(secret, os.environ["NEXUSS_BRIDGE_DATABASE"],
                      os.getenv("NEXUSS_TAS_LOCAL_URL", "http://127.0.0.1:8000"),
                      worker_database=os.getenv("NEXUSS_WORKER_DATABASE"),
                      operations_socket=os.getenv("NEXUSS_HOST_OPS_SOCKET"),
                      release_socket=os.getenv("NEXUSS_RELEASE_SOCKET"))
