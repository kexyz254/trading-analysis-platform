# Task 004: Add ATR Indicator

## Description
Add Average True Range (ATR) volatility indicator to the trading assistant.

## Location
File: `trading_assistant/core/indicators.py`

## Function Signature
```python
def calculate_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    pass
```

## Background
ATR measures market volatility. True Range = max(high-low, |high-prev_close|, |low-prev_close|)

## Requirements
1. Calculate True Range for each period
2. Calculate ATR = SMA of True Range over period
3. Return pandas Series of ATR values
4. Include docstring and type hints

## Tests Required
Add 3 tests:
- Test ATR returns positive values
- Test ATR increases with volatility
- Test insufficient data returns NaN

## Acceptance Criteria
- All existing tests pass
- New ATR tests pass
