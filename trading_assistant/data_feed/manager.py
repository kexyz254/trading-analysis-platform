import pandas as pd
import numpy as np
from typing import Dict, List, Callable, Optional
from datetime import datetime
from collections import deque

class DataFeedManager:
    def __init__(self, max_queue_size: int = 10000):
        self.subscribers: Dict[str, List[Callable]] = {}
        self.data_buffer: Dict[str, deque] = {}
        self.max_queue_size = max_queue_size
    
    def subscribe(self, symbol: str, callback: Callable):
        if symbol not in self.subscribers:
            self.subscribers[symbol] = []
            self.data_buffer[symbol] = deque(maxlen=self.max_queue_size)
        self.subscribers[symbol].append(callback)
    
    def publish(self, symbol: str, data: Dict):
        if symbol not in self.data_buffer:
            self.data_buffer[symbol] = deque(maxlen=self.max_queue_size)
        self.data_buffer[symbol].append(data)
        
        if symbol in self.subscribers:
            for callback in self.subscribers[symbol]:
                callback(data)
    
    def get_recent_data(self, symbol: str, n: int = 100) -> List[Dict]:
        if symbol in self.data_buffer:
            return list(self.data_buffer[symbol])[-n:]
        return []

class SimulatedDataFeed:
    def __init__(self, manager: DataFeedManager):
        self.manager = manager
        self.symbols = []
        self.prices = {}
        self.running = False
    
    def add_symbol(self, symbol: str, initial_price: float, volatility: float = 0.02):
        self.symbols.append(symbol)
        self.prices[symbol] = {
            'price': initial_price,
            'volatility': volatility,
            'trend': 0
        }
