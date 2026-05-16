import asyncio
import websocket
import json
import threading
from datetime import datetime
from typing import Callable, Dict, List, Optional
from dataclasses import dataclass

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
