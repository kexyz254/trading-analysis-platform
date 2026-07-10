from trading_assistant.agents.symbol_discovery import SymbolDiscoveryAgent


class FakeExchange:
    def __init__(self, markets, tickers):
        self._markets = markets
        self._tickers = tickers

    def load_markets(self):
        return self._markets

    def fetch_tickers(self):
        return self._tickers


def _market(quote="USDT", active=True, market_type="spot"):
    return {"quote": quote, "active": active, "type": market_type}


def _ticker(volume, bid=1.0, ask=1.01):
    return {"quoteVolume": volume, "bid": bid, "ask": ask}


def test_discover_filters_by_quote_currency():
    markets = {
        "BTC/USDT": _market(quote="USDT"),
        "BTC/BUSD": _market(quote="BUSD"),
    }
    tickers = {"BTC/USDT": _ticker(10_000_000), "BTC/BUSD": _ticker(10_000_000)}
    agent = SymbolDiscoveryAgent(FakeExchange(markets, tickers), quote_currency="USDT", min_24h_volume_usd=0)

    result = agent.discover()

    assert result == ["BTC/USDT"]


def test_discover_filters_by_minimum_volume():
    markets = {"BTC/USDT": _market(), "SHITCOIN/USDT": _market()}
    tickers = {"BTC/USDT": _ticker(10_000_000), "SHITCOIN/USDT": _ticker(1_000)}
    agent = SymbolDiscoveryAgent(FakeExchange(markets, tickers), min_24h_volume_usd=5_000_000)

    result = agent.discover()

    assert result == ["BTC/USDT"]


def test_discover_excludes_inactive_markets():
    markets = {"BTC/USDT": _market(active=True), "DELISTED/USDT": _market(active=False)}
    tickers = {"BTC/USDT": _ticker(10_000_000), "DELISTED/USDT": _ticker(10_000_000)}
    agent = SymbolDiscoveryAgent(FakeExchange(markets, tickers), min_24h_volume_usd=0)

    result = agent.discover()

    assert result == ["BTC/USDT"]


def test_discover_excludes_non_spot_markets():
    markets = {"BTC/USDT": _market(market_type="spot"), "BTC/USDT:USDT": _market(market_type="future")}
    tickers = {"BTC/USDT": _ticker(10_000_000), "BTC/USDT:USDT": _ticker(10_000_000)}
    agent = SymbolDiscoveryAgent(FakeExchange(markets, tickers), min_24h_volume_usd=0)

    result = agent.discover()

    assert result == ["BTC/USDT"]


def test_discover_ranks_by_volume_descending_and_caps_at_max_symbols():
    markets = {f"COIN{i}/USDT": _market() for i in range(5)}
    tickers = {f"COIN{i}/USDT": _ticker(volume=(i + 1) * 1_000_000) for i in range(5)}
    agent = SymbolDiscoveryAgent(FakeExchange(markets, tickers), min_24h_volume_usd=0, max_symbols=3)

    result = agent.discover()

    assert result == ["COIN4/USDT", "COIN3/USDT", "COIN2/USDT"]  # highest volume first, top 3 only


def test_discover_skips_symbols_with_no_ticker_data():
    markets = {"BTC/USDT": _market(), "NEWLISTING/USDT": _market()}
    tickers = {"BTC/USDT": _ticker(10_000_000)}  # NEWLISTING/USDT has no ticker yet
    agent = SymbolDiscoveryAgent(FakeExchange(markets, tickers), min_24h_volume_usd=0)

    result = agent.discover()

    assert result == ["BTC/USDT"]


def test_discover_excludes_symbols_with_no_live_order_book():
    """Regression: COS/USDT on testnet cleared the volume floor (trailing
    24h quoteVolume from stale/earlier activity) while reporting
    bid=None/ask=None right now - a dead market with no resting orders, not
    a genuinely tradeable one. Trailing volume alone isn't a sufficient
    liveness signal."""
    markets = {"BTC/USDT": _market(), "DEAD/USDT": _market()}
    tickers = {
        "BTC/USDT": _ticker(10_000_000),
        "DEAD/USDT": {"quoteVolume": 10_000_000, "bid": None, "ask": None},
    }
    agent = SymbolDiscoveryAgent(FakeExchange(markets, tickers), min_24h_volume_usd=0)

    result = agent.discover()

    assert result == ["BTC/USDT"]


def test_discover_excludes_symbols_with_zero_or_negative_bid_ask():
    markets = {"BTC/USDT": _market(), "BROKEN/USDT": _market()}
    tickers = {
        "BTC/USDT": _ticker(10_000_000),
        "BROKEN/USDT": {"quoteVolume": 10_000_000, "bid": 0.0, "ask": 0.0},
    }
    agent = SymbolDiscoveryAgent(FakeExchange(markets, tickers), min_24h_volume_usd=0)

    result = agent.discover()

    assert result == ["BTC/USDT"]
