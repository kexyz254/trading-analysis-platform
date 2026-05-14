# Task 012: Extract price validation

OHLCReader has validation code inside __init__

move _validate to standalone function

new function: validate_ohlc(df)

should raise ValueError if columns missing or prices invalid

update OHLCReader to use this function
