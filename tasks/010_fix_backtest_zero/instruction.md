# Task 010: Fix backtest division by zero
when price is zero, backtest engine crashes, check trading_assistant/backtesting/engine.py in run() method, 
skip trade if price <= 0 also log warning using print
