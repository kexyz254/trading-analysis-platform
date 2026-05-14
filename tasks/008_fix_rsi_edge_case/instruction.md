# Task 008: Fix RSI edge case

when prices are flat (no change), RSI returns NaN

should return 50 instead

fix in calculate_rsi function in trading_assistant/core/indicators.py

add small test for flat prices

hint: check when gain and loss are both zero
