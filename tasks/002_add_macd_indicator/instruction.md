# Task: Add MACD Indicator

Add MACD (Moving Average Convergence Divergence) to the indicators file.

## What to do

In `trading_assistant/core/indicators.py`, add a function called `calculate_macd`.

It should take:
- prices (pandas Series)
- fast period (default 12)
- slow period (default 26)  
- signal period (default 9)

It should return three things:
- macd line (fast ema minus slow ema)
- signal line (ema of macd line)
- histogram (macd line minus signal line)

## Tests needed

Add 3 test functions to `tests/test_indicators.py`:

1. Check that all three returned series have the same length as prices
2. Check that macd crosses above signal at some point with trending data
3. Check that first few values are NaN when not enough data

Make sure test function names start with `test_macd_`

## Acceptance

All tests pass. Keep the existing RSI tests working.
