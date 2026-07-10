import asyncio
import json
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

import pandas as pd
import websocket

@dataclass
class Tick:
    symbol: str
    price: float
    volume: int
    bid: float
    ask: float
    timestamp: datetime

@dataclass
class Candle:
    symbol: str
    timeframe: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    timestamp: datetime

_TIMEFRAME_UNIT_MS = {"m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}


def _timeframe_to_ms(timeframe: str) -> int:
    import re

    match = re.match(r"^(\d+)([mhdw])$", timeframe)
    if not match:
        raise ValueError(f"unsupported timeframe format: {timeframe!r}")
    quantity, unit = match.groups()
    return int(quantity) * _TIMEFRAME_UNIT_MS[unit]


class LiveFeed:
    """Fetch OHLCV data from a live source and return it as a pandas DataFrame."""

    def __init__(
        self,
        symbol: str = "BTC/USDT",
        timeframe: str = "1m",
        data_source: Optional[Callable[..., Any]] = None,
        exchange: Optional[Any] = None,
    ):
        self.symbol = symbol
        self.timeframe = timeframe
        self._data_source = data_source
        # If not injected, a fresh bare ccxt.binance() is built lazily below -
        # which is ALWAYS mainnet (never sandboxed). Callers running against
        # testnet (config.mode != "live") MUST inject the same already-
        # sandboxed exchange instance used for real execution, or this feed
        # silently trains/monitors/validates on a different order book than
        # the one it actually trades against. Found via an audit: run_system.py
        # was constructing MarketMonitorAgent/LearningAgent/StrategyManager
        # with no `feeds` override, so every one of them defaulted to this
        # bare mainnet path even while execution correctly ran on testnet.
        self._exchange = exchange

    def _get_exchange(self):
        # Reused across calls rather than constructed fresh each time - a
        # fresh ccxt instance per call would reset ccxt's own client-side
        # rate-limit pacing, which matters once fetch_ohlcv_history makes
        # several sequential paginated requests in a row.
        if self._exchange is None:
            try:
                import ccxt
            except ImportError as exc:  # pragma: no cover - exercised when ccxt is unavailable
                raise RuntimeError("ccxt is required to fetch live Binance OHLCV data") from exc
            # 20000ms mirrors ExchangeConfig.timeout_ms's default - this
            # fallback path has no Config object to read the real value
            # from (only reached when no exchange was injected at all), so
            # it hardcodes the same safe default rather than ccxt's own
            # 10s, which is too eager to time out for some REST calls and,
            # more importantly, was never a deliberate choice here.
            self._exchange = ccxt.binance({"enableRateLimit": True, "timeout": 20000})
        return self._exchange

    def _default_data_source(
        self, symbol: str, timeframe: str, limit: int, since: Optional[int] = None
    ) -> List[List[Any]]:
        exchange = self._get_exchange()
        return exchange.fetch_ohlcv(symbol=symbol, timeframe=timeframe, limit=limit, since=since)

    async def stream_ohlcv(self, limit: int = 100, timeframe: Optional[str] = None, interval: float = 1.0, max_bars: Optional[int] = None):
        """Yield OHLCV DataFrames continuously for a live trading loop."""
        bars_seen = 0
        while max_bars is None or bars_seen < max_bars:
            yield self.fetch_ohlcv(limit=limit, timeframe=timeframe)
            bars_seen += 1
            if max_bars is not None and bars_seen >= max_bars:
                break
            await asyncio.sleep(interval)

    def fetch_ohlcv(self, limit: int = 100, timeframe: Optional[str] = None, since: Optional[int] = None) -> pd.DataFrame:
        """Fetch the latest OHLCV bars (or bars starting at `since`, in ms) and
        convert them into a validated DataFrame."""
        source = self._data_source or self._default_data_source
        rows = source(self.symbol, timeframe or self.timeframe, limit=limit, since=since)

        if isinstance(rows, pd.DataFrame):
            frame = rows.copy()
        else:
            frame = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])

        if frame.empty:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

        if "timestamp" in frame.columns:
            frame = frame.copy()
            if pd.api.types.is_numeric_dtype(frame["timestamp"]):
                frame["timestamp"] = pd.to_datetime(frame["timestamp"], unit="ms", utc=True)
            else:
                frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
        else:
            frame.insert(0, "timestamp", pd.to_datetime(range(len(frame)), unit="ns"))

        for column in ["open", "high", "low", "close", "volume"]:
            if column in frame.columns:
                frame[column] = pd.to_numeric(frame[column], errors="coerce")

        return frame[["timestamp", "open", "high", "low", "close", "volume"]]

    def fetch_ohlcv_history(self, total_bars: int, page_size: int = 1000) -> pd.DataFrame:
        """Fetch more history than a single request allows, for walk-forward
        validation. Binance's REST kline endpoint caps a single request at
        ~1000 bars; this pages backward with `since`, oldest page last,
        stitching together enough non-overlapping pages to cover
        `total_bars`. Returns the most recent `total_bars` rows available
        (fewer if the symbol doesn't have that much history)."""
        bar_ms = _timeframe_to_ms(self.timeframe)
        pages: List[pd.DataFrame] = []
        collected = 0
        since: Optional[int] = None

        while collected < total_bars:
            page = self.fetch_ohlcv(limit=page_size, since=since)
            if page.empty:
                break
            pages.append(page)
            collected += len(page)

            earliest_ms = int(page["timestamp"].iloc[0].value // 1_000_000)
            next_since = earliest_ms - page_size * bar_ms
            if since is not None and next_since >= since:
                break  # not making backward progress - avoid looping forever
            since = next_since

            if len(page) < page_size:
                break  # reached the beginning of available history

        if not pages:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

        combined = pd.concat(pages, ignore_index=True)
        combined = combined.drop_duplicates(subset="timestamp").sort_values("timestamp").reset_index(drop=True)
        return combined.tail(total_bars).reset_index(drop=True)


class WebSocketFeed:
    def __init__(self):
        self.ws = None
        self.subscribers: Dict[str, List[Callable]] = {}
        self.running = False
        
    def connect(self, url: str):
        self.ws = websocket.WebSocketApp(url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close)
        threading.Thread(target=self.ws.run_forever, daemon=True).start()
    
    def _on_open(self, ws):
        self.running = True
        
    def _on_message(self, ws, message):
        data = json.loads(message)
        tick = Tick(
            symbol=data.get('s', ''),
            price=float(data.get('p', 0)),
            volume=int(data.get('v', 0)),
            bid=float(data.get('b', 0)),
            ask=float(data.get('a', 0)),
            timestamp=datetime.now()
        )
        for callback in self.subscribers.get(tick.symbol, []):
            callback(tick)
    
    def _on_error(self, ws, error):
        print(f"WebSocket error: {error}")
    
    def _on_close(self, ws, close_status_code, close_msg):
        self.running = False
    
    def subscribe(self, symbol: str, callback: Callable):
        if symbol not in self.subscribers:
            self.subscribers[symbol] = []
        self.subscribers[symbol].append(callback)
    
    def unsubscribe(self, symbol: str, callback: Callable):
        if symbol in self.subscribers:
            self.subscribers[symbol].remove(callback)

class SimulatedFeed:
    def __init__(self):
        self.subscribers: Dict[str, List[Callable]] = {}
        self.running = False
        self.prices: Dict[str, float] = {}
        self.base_prices = {
            'AAPL': 175.50, 'GOOGL': 135.20, 'MSFT': 375.80,
            'AMZN': 178.30, 'TSLA': 240.60, 'META': 485.90
        }
        self.volatility = 0.02
    
    def start(self):
        self.running = True
        threading.Thread(target=self._generate_ticks, daemon=True).start()
    
    def stop(self):
        self.running = False
    
    def _generate_ticks(self):
        import numpy as np
        import time
        while self.running:
            for symbol, base in self.base_prices.items():
                if symbol not in self.prices:
                    self.prices[symbol] = base
                
                change = np.random.normal(0, self.volatility)
                self.prices[symbol] *= (1 + change)
                
                tick = Tick(
                    symbol=symbol,
                    price=self.prices[symbol],
                    volume=int(np.random.uniform(100, 10000)),
                    bid=self.prices[symbol] * 0.999,
                    ask=self.prices[symbol] * 1.001,
                    timestamp=datetime.now()
                )
                for callback in self.subscribers.get(symbol, []):
                    callback(tick)
            time.sleep(0.1)
    
    def subscribe(self, symbol: str, callback: Callable):
        if symbol not in self.subscribers:
            self.subscribers[symbol] = []
        self.subscribers[symbol].append(callback)
    
    def unsubscribe(self, symbol: str, callback: Callable):
        if symbol in self.subscribers:
            self.subscribers[symbol].remove(callback)

class HistoricalDataLoader:
    def __init__(self, data_dir: str = "./data"):
        self.data_dir = data_dir
    
    def load_csv(self, filepath: str) -> List[Candle]:
        import pandas as pd
        df = pd.read_csv(filepath)
        candles = []
        for _, row in df.iterrows():
            candles.append(Candle(
                symbol=row.get('symbol', 'UNKNOWN'),
                timeframe=row.get('timeframe', '1min'),
                open=float(row['open']),
                high=float(row['high']),
                low=float(row['low']),
                close=float(row['close']),
                volume=int(row['volume']),
                timestamp=pd.to_datetime(row['timestamp'])
            ))
        return candles
    
    def fetch_yahoo(self, symbol: str, start: str, end: str, interval: str = '1d'):
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        df = ticker.history(start=start, end=end, interval=interval)
        candles = []
        for idx, row in df.iterrows():
            candles.append(Candle(
                symbol=symbol,
                timeframe=interval,
                open=float(row['Open']),
                high=float(row['High']),
                low=float(row['Low']),
                close=float(row['Close']),
                volume=int(row['Volume']),
                timestamp=idx.to_pydatetime()
            ))
        return candles
    
    def fetch_alpha_vantage(self, symbol: str, api_key: str, function: str = 'TIME_SERIES_DAILY'):
        import requests
        url = f'https://www.alphavantage.co/query?function={function}&symbol={symbol}&apikey={api_key}&outputsize=full'
        response = requests.get(url)
        data = response.json()
        candles = []
        time_series_key = f'Time Series (Daily)' if 'DAILY' in function else f'Time Series ({function.split("_")[-1]})'
        for date, values in data.get(time_series_key, {}).items():
            candles.append(Candle(
                symbol=symbol,
                timeframe='1d',
                open=float(values['1. open']),
                high=float(values['2. high']),
                low=float(values['3. low']),
                close=float(values['4. close']),
                volume=int(values['5. volume']),
                timestamp=datetime.strptime(date, '%Y-%m-%d')
            ))
        return candles
