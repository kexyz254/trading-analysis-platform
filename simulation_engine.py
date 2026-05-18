"""
Trade Simulation Engine
"""

class TradeSimulator:
    def __init__(self):
        self.trades = []

    def simulate(self, strategy, data):
        """
        Simulate trading signals over price data.
        strategy: an object with .generate_signal() method
        data: a list or pandas Series of prices
        """
        for idx in range(len(data)):
            price_window = data[:idx+1]
            signal = strategy.generate_signal(price_window)
            self.trades.append({
                "index": idx,
                "price": price_window[-1],
                "signal": signal
            })
        return self.trades

    def summary(self):
        buys = sum(1 for t in self.trades if t["signal"] == "BUY")
        sells = sum(1 for t in self.trades if t["signal"] == "SELL")
        holds = sum(1 for t in self.trades if t["signal"] == "HOLD")
        return {"total": len(self.trades), "buys": buys, "sells": sells, "holds": holds}