"""Read-only API for the authoritative simulated-bankroll ledger."""

from fastapi import APIRouter, HTTPException

from webapp.price_call import get_book

router = APIRouter()


@router.get("/api/pro/bankroll")
async def bankroll_stats():
    try:
        return get_book().bankroll_report(recent_limit=20)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
