# make macd faster
macd recalculates ema every time
same prices same period should cache result
add lru_cache decorator to helper function
keep same output values
