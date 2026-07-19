"""FastAPI backend for the custom HTML/CSS/JS trading dashboard.

Serves the static frontend plus a JSON API that reads exclusively from the
journal SQLite database, config.yaml, and the live exchange - the same
sources the system itself writes to. No synthetic data anywhere; empty
results are returned as empty lists, and the frontend is responsible for
saying "no data yet" rather than the backend faking a placeholder.

Usage:
    uvicorn webapp.server:app --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Optional, Any, Dict

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from trading_assistant.agents.strategy_manager import StrategyManager
from trading_assistant.config import load_config
from webapp import data_access as da
from webapp.bankroll_api import router as bankroll_router

STATIC_DIR = Path(__file__).resolve().parent / "static"

# -- Pro dashboard static directory --
PRO_STATIC_DIR = Path(__file__).resolve().parent / "pro_static"

_CACHE_BUSTED_ASSETS = ("style.css", "ws.js", "app.js", "chart.js")


def _static_asset_version() -> str:
    """A version string derived from the static assets' own mtimes, not a
    manually-maintained constant - browsers (mobile especially) can cache
    JS/CSS aggressively even across a real deploy. Confirmed live: after
    shipping a new app.js, a phone kept the OLD cached copy while
    index.html reloaded fresh - new UI elements rendered (from the fresh
    HTML) but nothing populated them (from the stale JS), which looks
    exactly like a broken feature rather than a caching issue. Computed
    once at import time, not per-request - these files don't change while
    the container is running."""
    mtimes = [(STATIC_DIR / name).stat().st_mtime for name in _CACHE_BUSTED_ASSETS if (STATIC_DIR / name).exists()]
    return str(int(max(mtimes))) if mtimes else "0"


_ASSET_VERSION = _static_asset_version()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="trading_system.db")
    parser.add_argument("--config", default=None)
    args, _ = parser.parse_known_args()
    return args


_args = _parse_args()
_config = load_config(_args.config)
_db_path = _args.db

logger = logging.getLogger(__name__)


async def _price_call_loop() -> None:
    """Background loop for the self-scoring 5-minute price calls (see
    webapp/price_call.py). Runs in this dashboard process so the track
    record accumulates 24/7 whether or not anyone is watching; each pass
    is run in a worker thread because it does blocking exchange I/O and
    (at most every 30 min per symbol) model training.

    CRITICAL: this writes to the dashboard-owned analytics database ONLY
    (price_call.DEFAULT_ANALYTICS_DB), never to trading_system.db - the
    dashboard must stay strictly read-only on the shared trading journal.
    See price_call.py's storage note for the production corruption this
    rule exists to prevent."""
    from webapp import price_call
    while True:
        try:
            await asyncio.to_thread(price_call.run_once, _config)
        except Exception:  # noqa: BLE001 - the loop must survive any single pass failing
            logger.exception("price call loop pass failed")
        await asyncio.sleep(60)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # The Task reference is deliberately held for the app's lifetime:
    # asyncio keeps only weak references to tasks, so a bare
    # create_task(...) can be garbage-collected mid-flight and the loop
    # silently stops - no exception, no log. Cancelled (and awaited) on
    # shutdown so SIGTERM doesn't kill a pass mid-write to analytics.db.
    task = asyncio.create_task(_price_call_loop())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="Trading System Dashboard API", lifespan=_lifespan)

# Apply CORS configurations to safely handle front-end client connections
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register the incoming bankroll sub-router channels cleanly
app.include_router(bankroll_router)


# ---------------------------------------------------------------------------
# Existing endpoints (unchanged)
# ---------------------------------------------------------------------------
@app.get("/api/status")
def status():
    return {
        "mode": _config.mode,
        "is_live": _config.is_live,
        "symbols": da.active_symbols(_db_path, _config.symbols),
        "config_seed_symbols": _config.symbols,
        "timeframe": _config.timeframe,
        "tournament_enabled": _config.tournament.enabled,
        "tournament_drive_live_trading": _config.tournament.drive_live_trading,
        # Real Binance public WebSocket endpoint the BROWSER connects to
        # directly for live ticker/kline updates - testnet vs mainnet must
        # match config.mode exactly, same as every other exchange touchpoint.
        "ws_base_url": (
            "wss://stream.binance.com:9443/ws" if _config.is_live else "wss://stream.testnet.binance.vision/ws"
        ),
    }


@app.get("/api/journal")
def journal():
    return da.journal_snapshot(_db_path, _config.is_live)


@app.get("/api/alerts")
def alerts(limit: int = 100):
    return da.recent_alerts(_db_path, limit=limit)


@app.get("/api/execution-quality")
def execution_quality():
    return da.execution_quality_summary(_db_path)


@app.get("/api/trade-audit")
def trade_audit(limit: int = 200):
    return da.trade_audit_records(_db_path, limit=limit)


@app.get("/api/health")
def health():
    return da.system_health(_db_path, poll_interval_seconds=_config.poll_interval_seconds)


@app.get("/api/retrain-events")
def retrain_events(limit: int = 100):
    return da.retrain_events(_db_path, limit=limit)


@app.get("/api/tournament-events")
def tournament_events(limit: int = 200):
    return da.tournament_events(_db_path, limit=limit)


@app.get("/api/markets")
def markets(quote: str = "USDT"):
    try:
        return da.market_overview(_config, quote_currency=quote)
    except Exception as exc:  # noqa: BLE001 - surface as a clean API error, not a 500 stack trace
        raise HTTPException(status_code=502, detail=f"Could not fetch market overview: {exc}") from exc


@app.get("/api/wallet-balance")
def wallet_balance(quote: str = "USDT"):
    try:
        return da.real_wallet_balance(_config, quote_currency=quote)
    except Exception as exc:  # noqa: BLE001 - surface as a clean API error, not a 500 stack trace
        raise HTTPException(status_code=502, detail=f"Could not fetch real wallet balance: {exc}") from exc


@app.get("/api/ohlcv")
def ohlcv(symbol: str, timeframe: str = "1h", limit: int = 300):
    if timeframe not in da.TIMEFRAMES:
        raise HTTPException(status_code=400, detail=f"timeframe must be one of {da.TIMEFRAMES}")
    try:
        return da.fetch_ohlcv(_config, symbol, timeframe, limit=limit)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Could not fetch OHLCV for {symbol}: {exc}") from exc


@app.get("/api/timeframes")
def timeframes():
    return da.TIMEFRAMES


@app.get("/api/paper-leaderboard")
def paper_leaderboard():
    symbols = da.active_symbols(_db_path, _config.symbols)
    return da.paper_leaderboard(_db_path, symbols, paper_capital=_config.tournament.paper_capital)


@app.get("/api/paper-equity")
def paper_equity(symbol: str, candidate: Optional[str] = None):
    return da.paper_equity_history(_db_path, symbol, candidate=candidate)


@app.get("/api/stability")
def stability():
    """Per symbol/regime tournament stability - the raw evidence, not just
    the pass/fail scorecard verdict (see /api/readiness for that)."""
    histories = da.winner_histories(_db_path)
    rows = []
    for symbol, by_regime in histories.items():
        for regime, history in by_regime.items():
            rows.append(
                {
                    "symbol": symbol,
                    "regime": regime,
                    "num_runs": len(history),
                    "stability_score": StrategyManager.compute_stability_score(history),
                    "current_winner": history[-1] if history else None,
                }
            )
    return rows


@app.get("/api/real-performance")
def real_performance():
    return da.real_portfolio_metrics(_db_path, _config.is_live)


@app.get("/api/readiness")
def readiness():
    symbols = da.active_symbols(_db_path, _config.symbols)
    return da.readiness_report(_db_path, _config.readiness, symbols=symbols, is_live=_config.is_live)


@app.get("/api/drift")
def drift():
    symbols = da.active_symbols(_db_path, _config.symbols)
    return da.drift_status(_db_path, symbols)


@app.get("/api/backtest-live-correlation")
def backtest_live_correlation():
    symbols = da.active_symbols(_db_path, _config.symbols)
    return da.backtest_live_correlation_records(
        _db_path, symbols, min_live_trades=_config.readiness.min_live_trades_for_correlation
    )


@app.get("/api/survivorship-bias")
def survivorship_bias():
    symbols = da.active_symbols(_db_path, _config.symbols)
    return da.survivorship_bias_records(
        _db_path, symbols, min_live_trades=_config.readiness.min_live_trades_for_correlation
    )


# ---------------------------------------------------------------------------
# NEW: Professional Dashboard API (read‑only, journal‑backed)
# ---------------------------------------------------------------------------
# Every {symbol} below uses the `:path` converter, not the default `str`
# one - symbols are exchange pairs like "BTC/USDT", and Starlette's default
# path converter refuses to match a "/" inside a single segment, so a plain
# {symbol} route 404s on every real symbol before the handler ever runs.

@app.get("/api/pro/ohlcv/{symbol:path}")
def pro_ohlcv(symbol: str, timeframe: str = "1h", limit: int = 200):
    """OHLCV bars the ENGINE persisted to the journal (its own configured
    timeframe only, one bar per poll cycle) - a research record of what the
    engine actually saw. NOT the chart's data source: the pro frontend
    charts from /api/ohlcv (exchange-backed, all timeframes)."""
    try:
        from trading_assistant.persistence.journal import Journal
        journal = Journal(_db_path, initialize=False)
        bars = journal.get_ohlcv_history(symbol, timeframe, limit)
        return {"symbol": symbol, "timeframe": timeframe, "bars": bars}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/api/pro/prediction/{symbol:path}")
def pro_prediction(symbol: str):
    """Latest real decision for the symbol (signal, confidence, regime, etc.)."""
    try:
        from trading_assistant.persistence.journal import Journal
        journal = Journal(_db_path, initialize=False)
        decisions = list(journal.recent_decisions(symbol=symbol, limit=1, exclude_paper=True))
        if not decisions:
            return {"signal": "HOLD", "confidence": None}
        d = decisions[0]
        details = d.get("details", {}) or {}
        return {
            "signal": d["signal"],
            "confidence": d.get("confidence"),
            "probability_up": details.get("probability_up"),
            "regime": details.get("regime"),
            "champion": details.get("champion"),
            "timestamp": d["timestamp"],
        }
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/api/pro/features/{symbol:path}")
def pro_features(symbol: str):
    """Latest SHAP feature importance for the champion model."""
    try:
        from trading_assistant.persistence.journal import Journal
        journal = Journal(_db_path, initialize=False)
        importance = journal.latest_feature_importance(symbol)
        if not importance:
            return {"features": []}
        sorted_features = sorted(importance["importance"].items(), key=lambda x: abs(x[1]), reverse=True)[:10]
        return {"features": [{"name": k, "importance": v} for k, v in sorted_features]}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/api/pro/trades/{symbol:path}")
def pro_trades(symbol: str, limit: int = 50):
    """Real trade history for the symbol."""
    try:
        from trading_assistant.persistence.journal import Journal
        journal = Journal(_db_path, initialize=False)
        trades = journal.recent_trades(symbol=symbol, limit=limit)
        return {"symbol": symbol, "trades": trades}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/api/pro/candidates/{symbol:path}")
def pro_candidates(symbol: str):
    """Paper‑trading leaderboard for the symbol (most recent snapshot per candidate)."""
    try:
        from trading_assistant.persistence.journal import Journal
        journal = Journal(_db_path, initialize=False)
        latest = journal.latest_paper_equity_by_candidate(symbol)
        candidates = []
        for name, snap in latest.items():
            candidates.append({
                "candidate": name,
                "capital": snap["capital"],
                "total_equity": snap["total_equity"],
                "num_trades": snap["num_trades"],
                "timestamp": snap["timestamp"],
            })
        return {"symbol": symbol, "candidates": candidates}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/api/pro/model-info/{symbol:path}")
def pro_model_info(symbol: str):
    """The system's REAL per-symbol model state - the promoted tournament
    champion / PredictionAgent output that actually drives live trading,
    not the dashboard's own analytics models. Everything here is read from
    the engine's own journal (the decisions/retrain_events/feature_importance
    it writes), so it reflects exactly what the live engine decided, on the
    engine's configured timeframe."""
    try:
        from trading_assistant.persistence.journal import Journal
        journal = Journal(_db_path, initialize=False)

        decisions = list(journal.recent_decisions(symbol=symbol, limit=1, exclude_paper=True))
        decision = decisions[0] if decisions else None
        details = (decision or {}).get("details", {}) or {}

        # Most recent retrain attempt for this symbol, and the most recent
        # one that actually promoted - "last retrain" vs "last promotion"
        # are different questions and the config notes promotions are rare.
        # da.retrain_events returns rows newest-first (ORDER BY id DESC in
        # _read_events), so iterate as returned and take the first match.
        last_retrain = None
        last_promotion = None
        for ev in da.retrain_events(_db_path, limit=500):
            if ev.get("symbol") not in (symbol, None):
                continue
            if last_retrain is None:
                last_retrain = ev
            if ev.get("promoted") and last_promotion is None:
                last_promotion = ev
            if last_retrain is not None and last_promotion is not None:
                break

        importance = journal.latest_feature_importance(symbol)
        feature_count = len(importance["importance"]) if importance else 0

        prob_up = details.get("probability_up")
        signal = decision["signal"] if decision else "HOLD"
        return {
            "symbol": symbol,
            "timeframe": _config.timeframe,
            "signal": signal,
            "champion": details.get("champion"),
            "regime": details.get("regime"),
            "confidence": decision.get("confidence") if decision else None,
            "probability_up": prob_up,
            "probability_down": (1 - prob_up) if prob_up is not None else None,
            "decision_at": decision["timestamp"] if decision else None,
            "buy_threshold": _config.prediction.buy_threshold,
            "sell_threshold": _config.prediction.sell_threshold,
            "feature_count": feature_count,
            "last_retrain_at": last_retrain["timestamp"] if last_retrain else None,
            "last_promotion_at": last_promotion["timestamp"] if last_promotion else None,
            "retrain_interval_hours": _config.learning.retrain_interval_hours,
        }
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/pro/price-calls/{symbol:path}")
async def pro_price_calls(symbol: str, limit: int = 20) -> Dict[str, Any]:
    """The symbol's current open 5-minute price call, its recent settled
    calls, and the running score - see webapp/price_call.py. Reads the
    dashboard-owned analytics db, NOT trading_system.db."""
    try:
        from webapp.price_call import get_book
        book = get_book()
        sym = symbol.upper()

        # Offload blocking SQLite disk I/O onto worker threads to keep event loop free
        open_call = await asyncio.to_thread(book.open_call, sym)
        recent = await asyncio.to_thread(book.recent_settled, sym, limit=limit)
        stats = await asyncio.to_thread(book.stats, sym)

        return {
            "symbol": sym,
            "open_call": open_call,
            "recent": recent,
            "stats": stats,
        }
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/pro/patterns/{symbol:path}")
def pro_patterns(symbol: str, timeframe: str = "1h"):
    """Classical chart patterns present at the right edge of the chart,
    with drawable trendline segments extended a few bars forward - see
    trading_assistant/analysis/chart_patterns.py. Confidence is geometry
    quality, not a prediction; the forecast models consume these same
    patterns as features, and their holdout hit rate is the honest measure
    of whether the patterns predict anything."""
    if timeframe not in da.TIMEFRAMES:
        raise HTTPException(status_code=400, detail=f"timeframe must be one of {da.TIMEFRAMES}")
    try:
        return {"symbol": symbol, "timeframe": timeframe, "patterns": da.detected_patterns(_config, symbol, timeframe)}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Could not detect patterns: {exc}") from exc


@app.get("/api/pro/forecast/{symbol:path}")
async def pro_forecast(symbol: str, timeframe: str = "1h") -> Dict[str, Any]:
    """Direction forecast for the next 1/2/3 closed candles of the given
    timeframe, each with its own out-of-sample hit rate - see
    webapp/forecast.py for what these numbers do and don't claim."""
    if timeframe not in da.TIMEFRAMES:
        raise HTTPException(status_code=400, detail=f"timeframe must be one of {da.TIMEFRAMES}")
    try:
        from webapp.forecast import get_forecast
        sym = symbol.upper()
        
        # CRITICAL: Prevent Random Forest model fitting from freezing the core async stream
        result = await asyncio.to_thread(get_forecast, _config, sym, timeframe)
        return result
    except Exception as exc:  # noqa: BLE001 - surface as a clean API error, not a 500 stack trace
        raise HTTPException(status_code=502, detail=f"Could not build forecast: {exc}") from exc


@app.get("/api/pro/decisions/{symbol:path}")
def pro_decisions(symbol: str, limit: int = 200):
    """Recent REAL decisions for the symbol - the prediction history the
    chart overlays as BUY/SELL markers on the candles they happened in."""
    try:
        from trading_assistant.persistence.journal import Journal
        journal = Journal(_db_path, initialize=False)
        decisions = journal.recent_decisions(symbol=symbol, limit=limit, exclude_paper=True)
        return {
            "symbol": symbol,
            "decisions": [
                {
                    "timestamp": d["timestamp"],
                    "signal": d["signal"],
                    "confidence": d["confidence"],
                    "price": d["price"],
                    "action": d["action"],
                }
                for d in decisions
            ]
        }
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/api/pro/position/{symbol:path}")
def pro_position(symbol: str):
    """Current real-portfolio position for the symbol, from the most recent
    mark-to-market equity snapshot - the same source of truth the engine
    itself trades against, not a separate/derived view."""
    try:
        from trading_assistant.persistence.journal import Journal
        journal = Journal(_db_path, initialize=False)
        snapshot = journal.latest_equity_snapshot()
        position = (snapshot or {}).get("positions", {}).get(symbol)
        if not snapshot or not position or not position.get("quantity"):
            return {"symbol": symbol, "open": False}
        return {
            "symbol": symbol,
            "open": True,
            "quantity": position["quantity"],
            "price": position["price"],
            "unexplained_quantity": position.get("unexplained_quantity", 0.0),
            "as_of": snapshot["timestamp"],
        }
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


# ---------------------------------------------------------------------------
# Existing root route (unchanged)
# ---------------------------------------------------------------------------

@app.get("/")
def index():
    # Inject ?v=<asset-mtime-version> onto every static asset reference so
    # a real deploy is never masked by a browser serving a stale cached
    # copy of app.js/style.css/etc. - see _static_asset_version's docstring.
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    for asset in _CACHE_BUSTED_ASSETS:
        html = html.replace(f'/static/{asset}"', f'/static/{asset}?v={_ASSET_VERSION}"')
    return HTMLResponse(html)


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ---------------------------------------------------------------------------
# NEW: Pro dashboard mount and route
# ---------------------------------------------------------------------------

# Mount the pro static assets (CSS, JS, etc.)
app.mount("/pro/static", StaticFiles(directory=str(PRO_STATIC_DIR)), name="pro_static")

# Same mtime-derived cache busting as the main dashboard (_static_asset_version
# above documents the real mobile incident that motivated it). ws.js lives in
# STATIC_DIR but is loaded by the pro page too, so it's versioned here as well.
_PRO_CACHE_BUSTED_ASSETS = ("style.css", "app.js")


def _pro_asset_version() -> str:
    paths = [PRO_STATIC_DIR / name for name in _PRO_CACHE_BUSTED_ASSETS]
    paths.append(STATIC_DIR / "ws.js")
    mtimes = [p.stat().st_mtime for p in paths if p.exists()]
    return str(int(max(mtimes))) if mtimes else "0"


_PRO_ASSET_VERSION = _pro_asset_version()


@app.get("/pro", response_class=HTMLResponse)
def pro_dashboard():
    """Serve the professional trading terminal."""
    html_path = PRO_STATIC_DIR / "index.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="Pro dashboard not found. Ensure webapp/pro_static/index.html exists.")
    html = html_path.read_text(encoding="utf-8")
    for asset in _PRO_CACHE_BUSTED_ASSETS:
        html = html.replace(f'/pro/static/{asset}"', f'/pro/static/{asset}?v={_PRO_ASSET_VERSION}"')
    html = html.replace('/static/ws.js"', f'/static/ws.js?v={_PRO_ASSET_VERSION}"')
    return HTMLResponse(html)
