# fix rsi division by zero

rsi crashes when gain and loss are both zero

prices that dont move cause this

should return 50 instead of crashing

look at calculate_rsi function

add check before division
