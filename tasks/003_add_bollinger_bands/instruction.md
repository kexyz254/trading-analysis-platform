# Task 003: Add Bollinger Bands Indicator

## Description
Add Bollinger Bands technical indicator to the trading assistant library.

## Location
File: `trading_assistant/core/indicators.py`

## Function Signature
```python
def calculate_bollinger_bands(prices: pd.Series, period: int = 20, num_std: int = 2):
    pass
```

## Requirements
1. Calculate middle band (SMA of prices)
2. Calculate standard deviation over period
3. Upper band = middle + (std * num_std)
4. Lower band = middle - (std * num_std)
5. Return tuple: (upper_band, middle_band, lower_band)

## Tests Required
Add 3 tests to `tests/test_indicators.py`:
- Verify all 3 bands are returned
- Verify upper >= middle >= lower after initial period
- Verify bands widen with higher volatility

## Acceptance Criteria
- All existing RSI and MACD tests still pass
- New Bollinger tests pass
- Code includes docstring and type hints
