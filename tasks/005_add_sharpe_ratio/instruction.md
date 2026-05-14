# Task 005: Add Sharpe Ratio Calculator

## Description
Add Sharpe Ratio to measure risk-adjusted returns.

## Location
File: `trading_assistant/core/indicators.py`

## Function Signature
```python
def calculate_sharpe_ratio(returns: pd.Series, risk_free_rate: float = 0.02, periods_per_year: int = 252) -> float:
    pass
```

## Formula
Sharpe = mean(excess_returns) / std(excess_returns) * sqrt(periods_per_year)

## Requirements
1. Calculate excess returns
2. Calculate Sharpe Ratio using formula
3. Return a single float value
4. Include docstring and type hints

## Tests Required
Add 3 tests:
- Test returns a float
- Test higher for better returns
- Test handles edge cases

## Acceptance Criteria
- All existing tests pass
- New Sharpe tests pass
