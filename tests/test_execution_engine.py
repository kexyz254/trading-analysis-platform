from execution_engine import ExecutionEngine

def test_execution_engine():
    prices = [100, 101, 102]
    signals = ["BUY", "HOLD", "SELL"]
    engine = ExecutionEngine()
    for signal, price in zip(signals, prices):
        pos = engine.execute(signal, price)
        print(f"Executed {signal} at {price}, position: {pos}")
    print("Orders:", engine.get_orders())
    print("Current position:", engine.current_position())

if __name__ == "__main__":
    test_execution_engine()