import pytest
from trading_assistant.data_feed.manager import DataFeedManager, SimulatedDataFeed

def test_data_feed_manager_subscribe():
    manager = DataFeedManager()
    called = False
    
    def callback(data):
        nonlocal called
        called = True
    
    manager.subscribe("AAPL", callback)
    manager.publish("AAPL", {"price": 100})
    assert called is True

def test_simulated_data_feed():
    manager = DataFeedManager()
    feed = SimulatedDataFeed(manager)
    feed.add_symbol("AAPL", 100, 0.02)
    feed.add_symbol("GOOGL", 150, 0.03)
    assert len(feed.symbols) == 2

def test_data_feed_get_recent():
    manager = DataFeedManager()
    for i in range(10):
        manager.publish("AAPL", {"price": 100 + i, "timestamp": i})
    recent = manager.get_recent_data("AAPL", 5)
    assert len(recent) == 5
