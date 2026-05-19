"""Advanced order book with price-time priority."""

from heapq import heappush, heappop
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
from datetime import datetime
import uuid

@dataclass(order=True)
class LimitOrder:
    priority: float
    order_id: str = field(compare=False)
    price: float = field(compare=False)
    quantity: float = field(compare=False)
    side: str = field(compare=False)
    timestamp: datetime = field(compare=False)
    order_type: str = field(compare=False, default="LIMIT")

@dataclass
class MarketOrder:
    order_id: str
    quantity: float
    side: str
    timestamp: datetime
    order_type: str = "MARKET"

class OrderBook:
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.bids = []  # max-heap using negative price
        self.asks = []  # min-heap
        self.orders: Dict[str, LimitOrder] = {}
        self.trades: List[Dict] = []
        self.last_price = None
        self.best_bid = None
        self.best_ask = None
    
    def add_limit_order(self, side: str, price: float, quantity: float) -> str:
        order_id = str(uuid.uuid4())
        order = LimitOrder(
            priority=-price if side == "BUY" else price,
            order_id=order_id,
            price=price,
            quantity=quantity,
            side=side,
            timestamp=datetime.now()
        )
        self.orders[order_id] = order
        
        if side == "BUY":
            heappush(self.bids, order)
            self.best_bid = max(self.bids).price if self.bids else None
        else:
            heappush(self.asks, order)
            self.best_ask = min(self.asks).price if self.asks else None
        
        self._match_orders()
        return order_id
    
    def add_market_order(self, side: str, quantity: float) -> str:
        order_id = str(uuid.uuid4())
        order = MarketOrder(
            order_id=order_id,
            quantity=quantity,
            side=side,
            timestamp=datetime.now()
        )
        
        remaining = quantity
        if side == "BUY":
            while remaining > 0 and self.asks:
                best_ask = self.asks[0]
                fill_qty = min(remaining, best_ask.quantity)
                self._execute_trade(best_ask, order, fill_qty, best_ask.price)
                remaining -= fill_qty
                if best_ask.quantity <= 0:
                    heappop(self.asks)
        else:
            while remaining > 0 and self.bids:
                best_bid = self.bids[0]
                fill_qty = min(remaining, best_bid.quantity)
                self._execute_trade(order, best_bid, fill_qty, best_bid.price)
                remaining -= fill_qty
                if best_bid.quantity <= 0:
                    heappop(self.bids)
        
        return order_id
    
    def cancel_order(self, order_id: str) -> bool:
        if order_id not in self.orders:
            return False
        order = self.orders[order_id]
        order.quantity = 0
        del self.orders[order_id]
        return True
    
    def _match_orders(self):
        matched = True
        while matched and self.bids and self.asks:
            best_bid = self.bids[0]
            best_ask = self.asks[0]
            
            if best_bid.price >= best_ask.price:
                fill_qty = min(best_bid.quantity, best_ask.quantity)
                price = best_ask.price
                self._execute_trade(best_bid, best_ask, fill_qty, price)
                
                if best_bid.quantity <= 0:
                    heappop(self.bids)
                if best_ask.quantity <= 0:
                    heappop(self.asks)
            else:
                matched = False
        
        self.best_bid = self.bids[0].price if self.bids else None
        self.best_ask = self.asks[0].price if self.asks else None
    
    def _execute_trade(self, buy_order, sell_order, quantity, price):
        buy_order.quantity -= quantity
        sell_order.quantity -= quantity
        self.last_price = price
        self.trades.append({
            'buy_order': buy_order.order_id,
            'sell_order': sell_order.order_id,
            'price': price,
            'quantity': quantity,
            'timestamp': datetime.now()
        })
    
    def get_market_depth(self, levels: int = 10) -> Dict:
        bids = []
        asks = []
        for i, bid in enumerate(self.bids):
            if i >= levels:
                break
            bids.append((bid.price, bid.quantity))
        for i, ask in enumerate(self.asks):
            if i >= levels:
                break
            asks.append((ask.price, ask.quantity))
        return {'bids': bids, 'asks': asks}
    
    def get_spread(self) -> Optional[float]:
        if self.best_bid and self.best_ask:
            return self.best_ask - self.best_bid
        return None
    
    def get_mid_price(self) -> Optional[float]:
        if self.best_bid and self.best_ask:
            return (self.best_bid + self.best_ask) / 2
        return self.last_price

class OrderManager:
    def __init__(self):
        self.order_books: Dict[str, OrderBook] = {}
        self.position_limits = {}
        self.daily_pnl = {}
    
    def add_symbol(self, symbol: str, position_limit: float = 1000):
        self.order_books[symbol] = OrderBook(symbol)
        self.position_limits[symbol] = position_limit
        self.daily_pnl[symbol] = 0
    
    def place_limit_order(self, symbol: str, side: str, price: float, quantity: float) -> str:
        if symbol not in self.order_books:
            self.add_symbol(symbol)
        return self.order_books[symbol].add_limit_order(side, price, quantity)
    
    def place_market_order(self, symbol: str, side: str, quantity: float) -> str:
        if symbol not in self.order_books:
            self.add_symbol(symbol)
        return self.order_books[symbol].add_market_order(side, quantity)
    
    def cancel_order(self, symbol: str, order_id: str) -> bool:
        if symbol in self.order_books:
            return self.order_books[symbol].cancel_order(order_id)
        return False
    
    def get_best_bid(self, symbol: str) -> Optional[float]:
        if symbol in self.order_books:
            return self.order_books[symbol].best_bid
        return None
    
    def get_best_ask(self, symbol: str) -> Optional[float]:
        if symbol in self.order_books:
            return self.order_books[symbol].best_ask
        return None
    
    def get_mid_price(self, symbol: str) -> Optional[float]:
        if symbol in self.order_books:
            return self.order_books[symbol].get_mid_price()
        return None
