# trading_assistant/execution_engine.py

"""
Order Execution Engine
"""

class ExecutionEngine:
    def __init__(self):
        self.orders = []
        self.position = 0

    def execute(self, trade_signal, price):
        if trade_signal == "BUY":
            self.orders.append({"action": "BUY", "price": price})
            self.position += 1
        elif trade_signal == "SELL":
            self.orders.append({"action": "SELL", "price": price})
            self.position -= 1
        else:
            self.orders.append({"action": "HOLD", "price": price})
        return self.position

    def get_orders(self):
        return self.orders

    def current_position(self):
        return self.position