# Task 007: Add EMA

add exponential moving average to indicators.py

function: ema(prices, period=20)

uses pandas ewm with adjust=False

tests needed:
- ema returns same length as input
- ema values differ from sma
