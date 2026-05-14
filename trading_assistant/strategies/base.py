"""Base strategy classes for trading."""
from abc import ABC, abstractmethod
from typing import Dict, Any
from enum import Enum

class Signal(Enum):
    """Trading signal enum."""
    BUY = 1
    SELL = -1
    HOLD = 0

class BaseStrategy(ABC):
    """Abstract base class for trading strategies."""
    
    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def generate_signal(self, data: Dict[str, Any]) -> Signal:
        """Generate trading signal based on market data."""
        pass
