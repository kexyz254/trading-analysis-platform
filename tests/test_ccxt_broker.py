from trading_assistant.execution.ccxt_broker import CcxtBrokerAdapter


class Exchange:
    def __init__(self):
        self.markets = {}
        self.loads = 0
        self.mutations = 0

    def load_markets(self):
        self.loads += 1
        self.markets = {
            "BTC/USDT": {
                "symbol": "BTC/USDT",
                "active": True,
                "base": "BTC",
                "quote": "USDT",
                "limits": {
                    "amount": {"min": .001},
                    "cost": {"min": 5},
                },
            }
        }
        return self.markets

    def market(self, symbol):
        return self.markets[symbol]

    def amount_to_precision(self, symbol, quantity):
        return f"{quantity:.3f}"

    def price_to_precision(self, symbol, price):
        return f"{price:.2f}"

    def create_order(self, *args, **kwargs):
        self.mutations += 1
        raise AssertionError("unexpected mutation")


def test_adapter_lazily_loads_market_metadata_once():
    exchange = Exchange()
    broker = CcxtBrokerAdapter(exchange)

    market = broker.market("BTC/USDT")
    amount = broker.normalize_amount("BTC/USDT", .0014)
    price = broker.normalize_price("BTC/USDT", 65000.129)

    assert market["base"] == "BTC"
    assert amount == .001
    assert price == 65000.13
    assert exchange.loads == 1
    assert exchange.mutations == 0
