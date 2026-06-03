import pytest
from trading_assistant.order_management.order_book import OrderBook, OrderManager

def test_order_book_add_limit_order():
    book = OrderBook("AAPL")
    order_id = book.add_limit_order("BUY", 100, 10)
    assert order_id is not None
    assert len(book.orders) == 1

def test_order_book_add_market_order():
    book = OrderBook("AAPL")
    book.add_limit_order("SELL", 100, 10)
    order_id = book.add_market_order("BUY", 5)
    assert order_id is not None

def test_order_book_cancel_order():
    book = OrderBook("AAPL")
    order_id = book.add_limit_order("BUY", 100, 10)
    result = book.cancel_order(order_id)
    assert result is True

def test_order_book_get_market_depth():
    book = OrderBook("AAPL")
    book.add_limit_order("BUY", 100, 10)
    book.add_limit_order("BUY", 99, 5)
    book.add_limit_order("SELL", 101, 8)
    depth = book.get_market_depth(2)
    assert len(depth['bids']) == 2
    assert len(depth['asks']) == 1

def test_order_manager_place_order():
    manager = OrderManager()
    order_id = manager.place_limit_order("AAPL", "BUY", 100, 10)
    assert order_id is not None