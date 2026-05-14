from abc import ABC, abstractmethod
from typing import Dict, Any
from enum import Enum

class Signal(Enum):
    BUY = 1
    SELL = -1
    HOLD = 0

class BaseStrategy(ABC):
    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def generate_signal(self, data: Dict[str, Any]) -> Signal:
        pass
