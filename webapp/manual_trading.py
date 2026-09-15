"""Read-only Manual Trading Workspace API.

This module intentionally exposes NO submit/cancel/amend route.
Phase 8A may inspect Binance and prepare orders, but cannot mutate the account.
"""

from __future__ import annotations

from dataclasses import asdict
from threading import Lock
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from trading_assistant.exchange_authority import ReadOnlyExchangeProxy
from trading_assistant.exchange_factory import build_exchange
from trading_assistant.execution.ccxt_broker import CcxtBrokerAdapter
from trading_assistant.execution.human_gateway import HumanOrderGateway
from trading_assistant.execution.human_risk import HumanRiskPolicy
from trading_assistant.execution.preview import HumanOrderPreviewService
from trading_assistant.market.catalog import MarketCatalog


class SubmitOrderRequest(BaseModel):
    confirmation_token: str = Field(min_length=20, max_length=4096)
    request_id: str = Field(min_length=8, max_length=128)
    symbol: str = Field(min_length=3, max_length=40)
    side: str
    order_type: str
    quantity: float = Field(gt=0)
    limit_price: Optional[float] = Field(default=None, gt=0)


class PrepareOrderRequest(BaseModel):
    request_id: str = Field(min_length=8, max_length=128)
    symbol: str = Field(min_length=3, max_length=40)
    side: str
    order_type: str
    quantity: float = Field(gt=0)
    limit_price: Optional[float] = Field(default=None, gt=0)


class _NoExecution:
    def submit(self, command):
        raise RuntimeError("Phase 8A has no execution authority")


def _confirmation_matches(
    confirmed: Any,
    operator_id: str,
    request: SubmitOrderRequest,
    normalized_quantity: float,
    normalized_limit_price: Optional[float],
) -> bool:
    return (
        confirmed.operator_id == operator_id
        and confirmed.request_id == request.request_id
        and confirmed.symbol == request.symbol.upper()
        and confirmed.side == request.side.lower()
        and confirmed.order_type == request.order_type.lower()
        and confirmed.quantity == float(normalized_quantity)
        and confirmed.limit_price == normalized_limit_price
    )


def build_manual_trading_router(config: Any, auth: Any, confirmations: Any) -> APIRouter:
    router = APIRouter(
        prefix="/api/pro/manual",
        tags=["Manual Trading Workspace"],
        dependencies=[Depends(auth.require_operator)],
    )

    lock = Lock()
    state: dict[str, Any] = {
        "exchange": None,
        "broker": None,
        "catalog": None,
    }

    def services():
        with lock:
            if state["broker"] is None:
                if config.execution_mode != "manual_assist":
                    raise RuntimeError(
                        "Phase 8A requires execution_mode=manual_assist"
                    )

                exchange = build_exchange(config)

                if not isinstance(exchange, ReadOnlyExchangeProxy):
                    raise RuntimeError(
                        "manual workspace refused non-read-only exchange"
                    )

                broker = CcxtBrokerAdapter(exchange)
                catalog = MarketCatalog()
                catalog.refresh(exchange)

                state["exchange"] = exchange
                state["broker"] = broker
                state["catalog"] = catalog

        return (
            state["exchange"],
            state["broker"],
            state["catalog"],
        )

    @router.get("/status")
    def manual_status():
        _, _, catalog = services()
        return {
            "mode": config.mode,
            "execution_mode": config.execution_mode,
            "live_execution_enabled": False,
            "workspace_phase": "8A_READ_ONLY",
            "market_count": len(catalog.symbols()),
        }

    @router.get("/markets")
    def manual_markets(
        q: str = "",
        quote: str = "USDT",
        limit: int = Query(default=50, ge=1, le=200),
    ):
        _, _, catalog = services()

        markets = catalog.search(
            query=q,
            quote=quote or None,
        )[:limit]

        return [asdict(market) for market in markets]

    @router.get("/quote/{symbol:path}")
    def manual_quote(symbol: str):
        _, broker, catalog = services()

        market = catalog.get(symbol)
        if market is None:
            raise HTTPException(
                status_code=404,
                detail="active market not found",
            )

        try:
            ticker = broker.fetch_ticker(symbol)
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail="live market quote temporarily unavailable",
            ) from exc

        return {
            "market": asdict(market),
            "ticker": {
                key: ticker.get(key)
                for key in (
                    "symbol", "bid", "ask", "last",
                    "high", "low", "baseVolume",
                    "quoteVolume", "percentage",
                )
            },
        }

    @router.get("/account")
    def manual_account(quote: str = "USDT"):
        _, broker, _ = services()

        try:
            balance = broker.fetch_balance()
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail="broker balance temporarily unavailable",
            ) from exc

        free = balance.get("free") or {}
        total = balance.get("total") or {}

        return {
            "quote": quote,
            "free_quote": float(free.get(quote) or 0),
            "total_quote": float(total.get(quote) or 0),
        }

    @router.post("/prepare")
    def prepare_order(
        request: PrepareOrderRequest,
        _session=Depends(auth.require_csrf),
    ):
        _, broker, catalog = services()

        if catalog.get(request.symbol) is None:
            raise HTTPException(
                status_code=400,
                detail="symbol is not an active Binance spot market",
            )

        gateway = HumanOrderGateway(
            HumanOrderPreviewService(broker),
            _NoExecution(),
            HumanRiskPolicy(
                max_spread_pct=config.risk.max_spread_pct,
            ),
        )

        try:
            prepared = gateway.prepare(
                symbol=request.symbol,
                side=request.side,
                order_type=request.order_type,
                quantity=request.quantity,
                limit_price=request.limit_price,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=str(exc),
            ) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail="pre-trade preview temporarily unavailable",
            ) from exc

        confirmation_token = None

        if prepared.risk.approved:
            confirmation_token = confirmations.issue(
                operator_id=_session.operator_id,
                request_id=request.request_id,
                symbol=request.symbol,
                side=request.side,
                order_type=request.order_type,
                quantity=prepared.preview.quantity,
                limit_price=(
                    prepared.preview.estimated_price
                    if request.order_type.lower() == "limit"
                    else None
                ),
            )

        return {
            "preview": asdict(prepared.preview),
            "risk": asdict(prepared.risk),
            "confirmation_token": confirmation_token,
            "execution_enabled": False,
        }

    @router.post("/submit")
    def submit_order(
        request: SubmitOrderRequest,
        session=Depends(auth.require_csrf),
    ):
        try:
            confirmed = confirmations.verify(
                request.confirmation_token
            )
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail="invalid or expired order confirmation",
            ) from exc

        _, broker, catalog = services()

        if catalog.get(request.symbol) is None:
            raise HTTPException(
                status_code=400,
                detail="symbol is not an active Binance spot market",
            )

        gateway = HumanOrderGateway(
            HumanOrderPreviewService(broker),
            _NoExecution(),
            HumanRiskPolicy(
                max_spread_pct=config.risk.max_spread_pct,
            ),
        )

        try:
            prepared = gateway.prepare(
                symbol=request.symbol,
                side=request.side,
                order_type=request.order_type,
                quantity=request.quantity,
                limit_price=request.limit_price,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=str(exc),
            ) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail="pre-trade revalidation temporarily unavailable",
            ) from exc

        normalized_limit_price = (
            prepared.preview.estimated_price
            if request.order_type.lower() == "limit"
            else None
        )

        if not _confirmation_matches(
            confirmed,
            session.operator_id,
            request,
            prepared.preview.quantity,
            normalized_limit_price,
        ):
            raise HTTPException(
                status_code=409,
                detail="order no longer matches its confirmation",
            )

        if not prepared.risk.approved:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "order no longer clears pre-trade risk",
                    "blockers": prepared.risk.blockers,
                },
            )

        if not config.manual_live_execution_enabled:
            raise HTTPException(
                status_code=503,
                detail="manual live execution is disabled",
            )

        # Phase 8C3 deliberately has no broker mutation path.
        raise HTTPException(
            status_code=503,
            detail="manual execution gateway not activated",
        )


    return router
