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
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from trading_assistant.agents.strategy_manager import StrategyManager
from trading_assistant.config import load_config
from webapp import data_access as da

STATIC_DIR = Path(__file__).resolve().parent / "static"

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

app = FastAPI(title="Trading System Dashboard API")


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
