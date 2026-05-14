# Trading Assistant

A Python library for backtesting trading strategies.

## Installation

```bash
pip install -e .
```

## Usage

```python
from trading_assistant.core.ohlc import OHLCReader
import pandas as pd

df = pd.read_csv("data.csv")
reader = OHLCReader(df)
bars = reader.get_bars()
```
