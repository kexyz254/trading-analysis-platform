"""OHLC data structures for trading."""
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional
import pandas as pd

@dataclass
class Bar:
    """Single price bar."""
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int

class OHLCReader:
    """Read and validate OHLC data."""
    
    def __init__(self, df: pd.DataFrame):
        self.df = self._validate(df)

    def _validate(self, df: pd.DataFrame) -> pd.DataFrame:
        required = ["open", "high", "low", "close", "volume"]
        missing = [c for c in required if c not in df.columns]
        if missing:
            raise ValueError(f"Missing columns: {missing}")
        return df

    def get_bars(self) -> List[Bar]:
        """Convert dataframe to list of Bar objects."""
        bars = []
        for idx, row in self.df.iterrows():
            bars.append(Bar(
                timestamp=idx,
                open=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
                volume=row["volume"]
            ))
        return bars
