"""Streamlit dashboard for live oversight of the trading system.

Reads exclusively from the journal SQLite database, config.yaml, and the
live exchange - every number and every candle shown here is real, not a
placeholder. If a section has no data yet, it says so rather than faking a
chart.

Usage:
    streamlit run dashboard.py -- --db trading_system.db --config config.yaml
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from trading_assistant.config import load_config
from trading_assistant.persistence.journal import Journal
from run_system import build_exchange

# Real Binance kline intervals only - no invented granularities.
TIMEFRAMES = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d"]

NAIROBI_TZ = ZoneInfo("Africa/Nairobi")


def _to_nairobi(value):
    """Convert a UTC timestamp (str or pandas-compatible) to Africa/Nairobi
    (EAT, UTC+3) for display. All storage stays UTC - only rendering changes."""
    ts = pd.to_datetime(value, utc=True)
    return ts.dt.tz_convert(NAIROBI_TZ) if hasattr(ts, "dt") else ts.tz_convert(NAIROBI_TZ)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="trading_system.db")
    parser.add_argument("--config", default=None)
    # Streamlit passes its own args through; ignore anything unrecognized
    # rather than erroring out on `streamlit run`'s own flags.
    args, _ = parser.parse_known_args()
    return args


@st.cache_resource
def _get_exchange(mode: str):
    config = load_config()
    return build_exchange(config)


@st.cache_data(ttl=30)
def _market_overview(_exchange, quote_currency: str) -> pd.DataFrame:
    """Full exchange ticker snapshot - real data pulled live from the exchange,
    not limited to the liquidity-filtered subset the system actually trades."""
    tickers = _exchange.fetch_tickers()
    rows = []
    for symbol, ticker in tickers.items():
        if not symbol.endswith(f"/{quote_currency}"):
            continue
        rows.append(
            {
                "symbol": symbol,
                "last": ticker.get("last"),
                "change_24h_pct": ticker.get("percentage"),
                "quote_volume_24h": ticker.get("quoteVolume"),
                "high_24h": ticker.get("high"),
                "low_24h": ticker.get("low"),
            }
        )
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("quote_volume_24h", ascending=False).reset_index(drop=True)
    return df


@st.cache_data(ttl=5)
def _fetch_ohlcv(_exchange, symbol: str, timeframe: str, limit: int = 300) -> pd.DataFrame:
    raw = _exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = _to_nairobi(pd.to_datetime(df["timestamp"], unit="ms"))
    return df.set_index("timestamp")


def _candlestick_chart(df: pd.DataFrame, symbol: str, timeframe: str) -> go.Figure:
    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, row_heights=[0.75, 0.25], vertical_spacing=0.03
    )
    fig.add_trace(
        go.Candlestick(
            x=df.index, open=df["open"], high=df["high"], low=df["low"], close=df["close"],
            name=symbol, increasing_line_color="#26a69a", decreasing_line_color="#ef5350",
        ),
        row=1, col=1,
    )
    for window, color in ((20, "#f0b90b"), (50, "#8e8ffa")):
        fig.add_trace(
            go.Scatter(
                x=df.index, y=df["close"].rolling(window).mean(), name=f"SMA {window}",
                line=dict(width=1.2, color=color),
            ),
            row=1, col=1,
        )
    bar_colors = ["#26a69a" if c >= o else "#ef5350" for o, c in zip(df["open"], df["close"])]
    fig.add_trace(go.Bar(x=df.index, y=df["volume"], name="Volume", marker_color=bar_colors), row=2, col=1)

    fig.update_layout(
        template="plotly_dark",
        height=560,
        margin=dict(l=10, r=10, t=30, b=10),
        title=f"{symbol} · {timeframe}",
        xaxis_rangeslider_visible=False,
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(showgrid=True, gridcolor="rgba(255,255,255,0.08)")
    return fig


@st.cache_data(ttl=5)
def _load_data(db_path: str) -> dict:
    journal = Journal(db_path=db_path)
    return {
        "equity": journal.recent_equity_snapshots(limit=2000),
        "decisions": journal.recent_decisions(limit=200),
        "trades": journal.recent_trades(limit=200),
        "circuit_breaker": journal.latest_circuit_breaker_event(),
    }


def _read_events(db_path: str, table: str, limit: int = 100) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(f"SELECT * FROM {table} ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()
    return [dict(row) for row in rows]


def _retrain_events(db_path: str, limit: int = 100) -> list[dict]:
    events = _read_events(db_path, "retrain_events", limit)
    for record in events:
        record["promoted"] = bool(record["promoted"])
        record["metrics"] = json.loads(record["metrics"]) if record["metrics"] else {}
    return events


def _tournament_events(db_path: str, limit: int = 100) -> list[dict]:
    events = _read_events(db_path, "tournament_events", limit)
    for record in events:
        record["winners"] = json.loads(record["winners"])
        record["report"] = json.loads(record["report"])
    return events


def _render_overview_tab(config, data, db_path: str) -> None:
    st.subheader("Equity curve")
    equity = data["equity"]
    if not equity:
        st.info("No equity snapshots recorded yet — the system needs at least one full cycle to mark to market.")
    else:
        eq_df = pd.DataFrame(equity)
        eq_df["timestamp"] = _to_nairobi(eq_df["timestamp"])
        eq_df = eq_df.set_index("timestamp")
        st.line_chart(eq_df[["total_equity", "capital"]])

        latest = equity[-1]
        first = equity[0]
        pnl_pct = (latest["total_equity"] - first["total_equity"]) / first["total_equity"] * 100.0
        m1, m2, m3 = st.columns(3)
        m1.metric("Current equity", f"${latest['total_equity']:,.2f}")
        m2.metric("Cash (capital)", f"${latest['capital']:,.2f}")
        m3.metric("Return since first snapshot", f"{pnl_pct:+.2f}%")

        positions = latest.get("positions") or {}
        st.subheader("Open positions")
        if positions:
            pos_df = pd.DataFrame([{"symbol": sym, **info} for sym, info in positions.items()])
            st.dataframe(pos_df, use_container_width=True)
        else:
            st.caption("No open positions as of the latest snapshot.")

    st.subheader("Circuit breaker")
    cb = data["circuit_breaker"]
    if cb is None:
        st.caption("No circuit breaker events recorded — never tripped.")
    elif cb["tripped"]:
        st.error(f"TRIPPED at {_to_nairobi(cb['timestamp'])} EAT — {cb['reason']}")
    else:
        st.success(f"OK (last reset at {_to_nairobi(cb['timestamp'])} EAT)")

    st.subheader("Recent decisions")
    decisions = data["decisions"]
    if not decisions:
        st.info("No decisions recorded yet.")
    else:
        dec_df = pd.DataFrame(decisions)
        dec_df["timestamp"] = _to_nairobi(dec_df["timestamp"])
        dec_df["regime"] = dec_df["details"].apply(lambda d: d.get("regime") if isinstance(d, dict) else None)
        dec_df["champion"] = dec_df["details"].apply(lambda d: d.get("champion") if isinstance(d, dict) else None)
        display_cols = [
            "timestamp", "symbol", "signal", "confidence", "regime",
            "risk_approved", "risk_reason", "action", "price", "quantity", "champion",
        ]
        st.dataframe(dec_df[display_cols], use_container_width=True, height=300)

    st.subheader("Recent trades")
    trades = data["trades"]
    if not trades:
        st.info("No trades executed yet.")
    else:
        trades_df = pd.DataFrame(trades)
        trades_df["timestamp"] = _to_nairobi(trades_df["timestamp"])
        realized = trades_df["pnl"].dropna()
        if not realized.empty:
            st.metric("Cumulative realized PnL", f"${realized.sum():,.2f}")
        st.dataframe(trades_df, use_container_width=True, height=300)

    st.subheader("Model health (walk-forward retrain history)")
    retrain_events = _retrain_events(db_path)
    if not retrain_events:
        st.info("No retrain attempts recorded yet.")
    else:
        rt_df = pd.DataFrame(retrain_events)
        rt_df["timestamp"] = _to_nairobi(rt_df["timestamp"])
        rt_df["median_sharpe"] = rt_df["metrics"].apply(lambda m: m.get("median_sharpe"))
        rt_df["pct_folds_profitable"] = rt_df["metrics"].apply(lambda m: m.get("pct_folds_profitable"))
        rt_df["num_folds"] = rt_df["metrics"].apply(lambda m: m.get("num_folds"))
        st.dataframe(
            rt_df[["timestamp", "promoted", "reason", "median_sharpe", "pct_folds_profitable", "num_folds"]],
            use_container_width=True, height=250,
        )

    st.subheader("Strategy tournament — stability tracking")
    tournament_events = _tournament_events(db_path)
    if not tournament_events:
        st.info("No tournament runs recorded yet (tournament.enabled is off, or none have run).")
    else:
        latest_by_symbol: dict[str, dict] = {}
        for event in tournament_events:
            latest_by_symbol.setdefault(event["symbol"], event)
        rows = []
        for symbol, event in latest_by_symbol.items():
            for regime, winner in event["winners"].items():
                rows.append({"symbol": symbol, "regime": regime, "current_winner": winner or "No Champion"})
        st.dataframe(pd.DataFrame(rows), use_container_width=True)
        st.caption(
            "Shows only the latest tournament winner per symbol/regime. Stability across "
            "repeated runs is what StrategyManager.stability_score() tracks in-process — "
            "not yet persisted here."
        )


def _render_markets_tab(config, exchange) -> None:
    st.caption(
        "Live from the exchange - every pair currently listed, not limited to the "
        "liquidity-filtered subset the system actually trades/learns on."
        + ("" if config.is_live else " Testnet mode: this reflects Binance testnet's own (smaller) market list, not mainnet.")
    )

    try:
        overview = _market_overview(exchange, config.symbol_discovery.quote_currency or "USDT")
    except Exception as exc:  # noqa: BLE001 - a dashboard fetch failure must not crash the page
        st.warning(f"Could not fetch live market overview: {exc}")
        return

    if overview.empty:
        st.info("No market data available.")
        return

    tracked = set(config.symbols)
    overview = overview.copy()
    overview["tracked"] = overview["symbol"].isin(tracked)

    f1, f2, f3, f4 = st.columns([2, 1.2, 1.2, 1.2])
    search = f1.text_input("Search symbol", "")
    min_volume = f2.number_input("Min 24h volume ($)", min_value=0.0, value=0.0, step=1_000_000.0, format="%.0f")
    sort_by = f3.selectbox("Sort by", ["24h Volume", "24h % Change", "Price"])
    direction = f4.selectbox("Direction", ["Descending", "Ascending"])

    filtered = overview
    if search:
        filtered = filtered[filtered["symbol"].str.contains(search.upper(), case=False)]
    if min_volume > 0:
        filtered = filtered[filtered["quote_volume_24h"] >= min_volume]

    sort_col = {"24h Volume": "quote_volume_24h", "24h % Change": "change_24h_pct", "Price": "last"}[sort_by]
    filtered = filtered.sort_values(sort_col, ascending=(direction == "Ascending"))

    st.caption(f"{len(filtered)} of {len(overview)} pairs shown.")
    st.dataframe(
        filtered.style.map(
            lambda v: "color: #26a69a" if isinstance(v, (int, float)) and v > 0 else "color: #ef5350",
            subset=["change_24h_pct"],
        ),
        use_container_width=True, height=450,
    )


def _render_chart_tab(config, exchange) -> None:
    try:
        overview = _market_overview(exchange, config.symbol_discovery.quote_currency or "USDT")
    except Exception as exc:  # noqa: BLE001
        st.warning(f"Could not fetch market list: {exc}")
        overview = pd.DataFrame()

    tracked = list(config.symbols)
    all_symbols = list(overview["symbol"]) if not overview.empty else tracked
    symbol_options = list(dict.fromkeys(tracked + all_symbols))

    c1, c2, c3 = st.columns([2, 3, 1.3])
    selected_symbol = c1.selectbox("Symbol", symbol_options)
    default_tf_index = TIMEFRAMES.index(config.timeframe) if config.timeframe in TIMEFRAMES else TIMEFRAMES.index("1h")
    selected_timeframe = c2.radio("Timeframe", TIMEFRAMES, index=default_tf_index, horizontal=True)
    live = c3.checkbox("🔴 Live", value=False, help="Auto-refresh this chart every few seconds with real exchange data.")

    try:
        ohlcv = _fetch_ohlcv(exchange, selected_symbol, selected_timeframe, limit=300)
    except Exception as exc:  # noqa: BLE001
        st.warning(f"Could not fetch OHLCV for {selected_symbol} @ {selected_timeframe}: {exc}")
        return

    if ohlcv.empty:
        st.info("No candle data available for this symbol/timeframe.")
        return

    st.plotly_chart(_candlestick_chart(ohlcv, selected_symbol, selected_timeframe), use_container_width=True)

    latest_close = ohlcv["close"].iloc[-1]
    change_over_window = (latest_close - ohlcv["close"].iloc[0]) / ohlcv["close"].iloc[0] * 100.0
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Last", f"{latest_close:,.4f}")
    m2.metric(f"Change ({len(ohlcv)} bars)", f"{change_over_window:+.2f}%")
    m3.metric("24h high", f"{ohlcv['high'].max():,.4f}")
    m4.metric("24h low", f"{ohlcv['low'].min():,.4f}")
    if selected_symbol in tracked:
        st.caption(f"{selected_symbol} is in the system's actively traded/learned universe.")
    else:
        st.caption(f"{selected_symbol} is shown for monitoring only — not currently traded by the system.")

    if live:
        last_candle_time = ohlcv.index[-1].strftime("%H:%M:%S")
        st.caption(f"Live mode: refreshing every 5s from the exchange. Last candle close: {last_candle_time} EAT.")
        time.sleep(5)
        st.rerun()


def main() -> None:
    args = _parse_args()
    st.set_page_config(page_title="Trading System Dashboard", layout="wide", page_icon="📈")

    config = load_config(args.config)

    st.title("Autonomous Trading System")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Mode", config.mode.upper())
    col2.metric("Symbols tracked", len(config.symbols))
    col3.metric("Tournament", "ON (observation)" if config.tournament.enabled else "off")
    col4.metric("Timeframe", config.timeframe)
    if config.is_live:
        st.error("LIVE mode - real funds are at risk.")

    if st.button("Refresh now"):
        st.cache_data.clear()

    try:
        exchange = _get_exchange(config.mode)
    except Exception as exc:  # noqa: BLE001
        exchange = None
        st.error(f"Could not connect to the exchange: {exc}")

    tab_overview, tab_markets, tab_chart = st.tabs(["Overview", "Markets", "Chart"])

    with tab_overview:
        data = _load_data(args.db)
        _render_overview_tab(config, data, args.db)

    with tab_markets:
        if exchange is not None:
            _render_markets_tab(config, exchange)

    with tab_chart:
        if exchange is not None:
            _render_chart_tab(config, exchange)

    now_nairobi = datetime.now(timezone.utc).astimezone(NAIROBI_TZ)
    st.caption(f"Dashboard rendered at {now_nairobi.strftime('%Y-%m-%d %H:%M:%S')} EAT (Nairobi) · reading {args.db}")


if __name__ == "__main__":
    main()
