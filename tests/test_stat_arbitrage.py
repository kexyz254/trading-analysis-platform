import pandas as pd
from stat_arbitrage import StatArbStrategy

def test_stat_arb_signal():
    prices = pd.Series([100, 102, 101, 99, 98, 100, 102, 104, 106, 105, 104, 103, 102, 101, 100, 99, 98, 97, 96, 95])
    strategy = StatArbStrategy(lookback=5, threshold=1.0)
    signal = strategy.generate_signal(prices)
    print(f"StatArb signal: {signal}")

if __name__ == "__main__":
    test_stat_arb_signal()