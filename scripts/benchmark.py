import time
import pandas as pd
import numpy as np
from trading_assistant.core.indicators import *

prices = pd.Series(np.random.randn(10000).cumsum() + 100)

start = time.time()
rsi = calculate_rsi(prices)
print(f"RSI: {time.time()-start:.3f}s")

start = time.time()
macd = calculate_macd(prices)
print(f"MACD: {time.time()-start:.3f}s")
