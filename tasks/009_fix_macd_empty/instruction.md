# Task 009: Fix MACD with empty data

calculate_macd crashes when prices is empty series

add check at start of function

if len(prices) == 0: return empty series

also check for None input
