# Task 008: Fix backtest position tracking

the backtest engine in trading_assistant/backtesting/engine.py has a bug

when you buy and sell multiple times, the capital gets messed up

find the bug and fix it

the problem: after first sell, capital is correct but next buy uses wrong amount

hint: look at how capital and position are updated

tests should pass after fix:
- test_multiple_trades_maintains_capital
- test_position_resets_after_sell
