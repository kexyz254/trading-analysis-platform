cat > webapp/bankroll_api.py << 'EOF'
"""Read-only API for the simulated price-call bankroll engine."""

from fastapi import APIRouter, HTTPException
from trading_assistant.analytics.price_call_bankroll import PriceCallBankroll

router = APIRouter()

@router.get("/api/pro/bankroll")
async def bankroll_stats():
    try:
        engine = PriceCallBankroll()
        engine.sync()
        return {
            "5m": engine.get_stats("5m"),
            "15m": engine.get_stats("15m"),
            "trades_5m": engine.get_all_trades("5m")[-20:],
            "trades_15m": engine.get_all_trades("15m")[-20:],
        }
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))
EOF