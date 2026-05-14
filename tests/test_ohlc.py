import pytest
import pandas as pd
from trading_assistant.core.ohlc import OHLCReader, Bar

def test_ohlc_reader_creates_bars():
    dates = pd.date_range("2024-01-01", periods=5, freq="D")
    df = pd.DataFrame({
        "open": [100, 101, 102, 103, 104],
        "high": [105, 106, 107, 108, 109],
        "low": [99, 100, 101, 102, 103],
        "close": [101, 102, 103, 104, 105],
        "volume": [1000, 1100, 1200, 1300, 1400]
    }, index=dates)

    reader = OHLCReader(df)
    bars = reader.get_bars()

    assert len(bars) == 5
    assert isinstance(bars[0], Bar)
    assert bars[0].close == 101

def test_ohlc_reader_validates_columns():
    df = pd.DataFrame({"open": [1, 2], "high": [3, 4]})
    with pytest.raises(ValueError, match="Missing columns"):
        OHLCReader(df)
