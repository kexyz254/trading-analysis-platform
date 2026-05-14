# Task 014: Optimize stochastic

stochastic_k uses rolling().min() and rolling().max()

this calculates same window multiple times

use expanding() and shift() for better performance

should be faster for large datasets

keep same output values
