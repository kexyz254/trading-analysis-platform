"""Separate supervisory gateway; fixed reads and durable advisory feedback only."""
import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import quote

import httpx
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from .protocol import Auth, MAX_BODY
from .worker import ObservationStore

# Intentionally projected fields: arbitrary logs, configuration and raw free-text
# records are not exported because they may contain credentials.
READS = {
    "health": ("/api/health", {"score", "label", "uptime_seconds", "last_heartbeat_at",
                                "last_heartbeat_status", "staleness_seconds",
                                "recent_error_rate", "circuit_breaker_tripped"}),
    "status": ("/api/status", {"mode", "is_live", "symbols", "timeframe"}),
    "incident": ("/api/journal", set()),
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
    stamp = None
    try:
        parsed = datetime.fromisoformat(event.get("timestamp"))
        if parsed.tzinfo:
            stamp = parsed.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError):
        pass
    return {"reason_code": "execution_errors" if match else "other", "event_at": stamp,
            "error_count": int(match[1]) if match else None}


class Feedback(BaseModel):
    model_config = ConfigDict(extra="forbid")
    feedback_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    kind: Literal["research", "incident", "repair_proposal", "evaluation"]
    summary: str = Field(min_length=1, max_length=4000)
    sources: list[HttpUrl] = Field(default_factory=list, max_length=20)
    observed_at: datetime
    expires_at: datetime
    execution_requested: Literal[False] = False


def project(value, keys):
    if isinstance(value, dict):
        return {k: project(v, keys) for k, v in value.items() if k in keys}
    if isinstance(value, list):
        return [project(v, keys) for v in value[:200]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return None


def create_app(secret, database, upstream="http://127.0.0.1:8000", transport=None, worker_database=None):
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
                "maintenance_execution": False, "schema_version": 1,
                "offline_observations": True}

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
                      worker_database=os.getenv("NEXUSS_WORKER_DATABASE"))
