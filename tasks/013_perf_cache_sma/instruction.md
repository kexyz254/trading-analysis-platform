# Task 013: Add caching to SMA

sma recalculates same inputs many times

add functools.lru_cache to sma function

cache maxsize=128

same prices and period should return cached result

add test to verify caching works
