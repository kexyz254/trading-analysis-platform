"""Read-only UMIL API.  No ATS router imports and no execution endpoints."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from .authority import AUTHORITY
from .config import Settings
from .service import UniverseIntelligenceService
from .versions import VERSIONS


def create_app(
    settings: Settings | None = None,
    service: UniverseIntelligenceService | None = None,
):
    try:
        from fastapi import FastAPI, HTTPException, Query, Request
        from fastapi.encoders import jsonable_encoder
        from fastapi.responses import JSONResponse
        from fastapi.staticfiles import StaticFiles
    except ImportError as exc:  # pragma: no cover - deployment dependency check
        raise RuntimeError("FastAPI is required to serve UMIL") from exc

    settings = settings or Settings.from_environment()
    service = service or UniverseIntelligenceService(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if settings.auto_start:
            await service.start()
        try:
            yield
        finally:
            await service.stop()

    app = FastAPI(
        title="Universe Market Intelligence Layer",
        version=VERSIONS.service_version,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/v1/schema/openapi.json",
        lifespan=lifespan,
    )
    app.state.umil_service = service
    app.state.umil_settings = settings

    @app.middleware("http")
    async def secure_read_only_responses(request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            return JSONResponse(
                status_code=405,
                content={"detail": "UMIL is a read-only service"},
                headers={"Allow": "GET, HEAD, OPTIONS"},
            )
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; connect-src 'self'; img-src 'self' data:; "
            "style-src 'self'; script-src 'self'; frame-ancestors 'none'"
        )
        response.headers["X-UMIL-Authority"] = "read-only"
        response.headers["X-Request-ID"] = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        return response

    @app.get("/health/live")
    async def liveness() -> dict[str, Any]:
        return {"service": "umil", "live": True, "version": VERSIONS.service_version}

    @app.get("/health/ready")
    async def readiness() -> dict[str, Any]:
        report = service.health()
        if report["state"] in {"WARMING", "STALE", "INVALID", "UNAVAILABLE"}:
            raise HTTPException(status_code=503, detail=jsonable_encoder(report))
        return report

    @app.get("/api/v1/authority")
    async def authority() -> dict[str, Any]:
        return {"authority": AUTHORITY.as_dict(), "versions": VERSIONS.as_dict()}

    @app.get("/api/v1/market-universe")
    async def market_universe(
        quote_asset: str | None = None,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, Any]:
        items = service.ledger.list_instruments(
            quote_asset=quote_asset, offset=offset, limit=limit
        )
        return {
            "api_schema_version": VERSIONS.api_schema_version,
            "offset": offset,
            "limit": limit,
            "items": items,
            "authority": AUTHORITY.as_dict(),
        }

    @app.get("/api/v1/market-intelligence")
    async def market_intelligence(
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, Any]:
        return service.market_rows(offset=offset, limit=limit)

    @app.get("/api/v1/market-intelligence/{symbol}")
    async def instrument_intelligence(symbol: str) -> dict[str, Any]:
        try:
            snapshot = service.latest_intelligence(symbol)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="instrument not found") from exc
        if snapshot is None:
            raise HTTPException(
                status_code=503,
                detail={"state": "WARMING", "symbol": symbol, "reason": "history_not_ready"},
            )
        return snapshot

    @app.get("/api/v1/market-dna/{symbol}")
    async def market_dna(symbol: str) -> dict[str, Any]:
        snapshot = await instrument_intelligence(symbol)
        return {
            "instrument_id": snapshot["instrument_id"],
            "as_of": snapshot["as_of"],
            "state": snapshot["state"],
            "clarity": snapshot["clarity"],
            "confidence": snapshot["confidence"],
            "dna": snapshot["dna"],
            "dimensions": snapshot["dimensions"],
            "quality_flags": snapshot["quality_flags"],
            "versions": snapshot["versions"],
            "authority": snapshot["authority"],
        }

    @app.get("/api/v1/market-insights/{symbol}")
    async def market_insights(symbol: str) -> dict[str, Any]:
        snapshot = await instrument_intelligence(symbol)
        return {
            "instrument_id": snapshot["instrument_id"],
            "as_of": snapshot["as_of"],
            "commentary": snapshot["commentary"],
            "evidence_record_id": snapshot["evidence_record_id"],
            "authority": snapshot["authority"],
        }

    @app.get("/api/v1/market-anomalies")
    async def market_anomalies(
        limit: int = Query(default=25, ge=1, le=100),
    ) -> dict[str, Any]:
        return {
            "api_schema_version": VERSIONS.api_schema_version,
            "items": service.market_anomalies(limit=limit),
            "authority": AUTHORITY.as_dict(),
        }

    @app.get("/api/v1/market-scale/{symbol}")
    async def market_scale(symbol: str) -> dict[str, Any]:
        try:
            result = service.market_scale(symbol)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="instrument not found") from exc
        if result is None:
            raise HTTPException(status_code=503, detail="scale evidence is warming")
        return {"scale": result, "authority": AUTHORITY.as_dict()}

    @app.get("/api/v1/market-scale")
    async def market_scale_leaders(
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, Any]:
        return {
            "api_schema_version": VERSIONS.api_schema_version,
            "items": service.scale_leaders(limit=limit),
            "authority": AUTHORITY.as_dict(),
        }

    @app.get("/api/v1/historical/intelligence/{symbol}")
    async def historical_intelligence(symbol: str, as_of: datetime) -> dict[str, Any]:
        if as_of.tzinfo is None:
            raise HTTPException(status_code=400, detail="as_of must include a timezone")
        try:
            instrument = service._resolve_instrument(symbol)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="instrument not found") from exc
        record = service.ledger.latest_as_of(
            event_type="MARKET_INTELLIGENCE_SNAPSHOT",
            instrument_id=instrument.instrument_id,
            as_of=as_of,
        )
        if record is None:
            raise HTTPException(status_code=404, detail="no intelligence existed by that time")
        return {
            "requested_as_of": as_of,
            "knowledge_cutoff_field": "received_at",
            "evidence": record.as_dict(),
            "authority": AUTHORITY.as_dict(),
        }

    @app.get("/api/v1/market-breadth")
    async def market_breadth() -> dict[str, Any]:
        snapshot = service.latest_breadth()
        if snapshot is None:
            raise HTTPException(status_code=503, detail="UMIL is warming")
        return snapshot

    @app.get("/api/v1/evidence/{evidence_id}")
    async def evidence_lineage(evidence_id: str) -> dict[str, Any]:
        try:
            uuid.UUID(evidence_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid evidence identity") from exc
        record = service.ledger.get(evidence_id)
        if record is None:
            raise HTTPException(status_code=404, detail="evidence not found")
        return record.as_dict(include_payload=False)

    dashboard = Path(__file__).resolve().parents[1] / "dashboard"
    if dashboard.is_dir():
        app.mount("/dashboard", StaticFiles(directory=dashboard, html=True), name="dashboard")

    return app
