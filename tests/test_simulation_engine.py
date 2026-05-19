import pandas as pd
from stat_arbitrage import StatArbStrategy
from simulation_engine import TradeSimulator

def test_simulation_engine():
    prices = pd.Series([100, 101, 102, 103, 104, 105, 106])
    strategy = StatArbStrategy(lookback=3, threshold=0.5)
    simulator = TradeSimulator()
    trades = simulator.simulate(strategy, prices)
    print("Simulation trades:", trades)
    print("Simulation summary:", simulator.summary())

if __name__ == "__main__":
    test_simulation_engine()