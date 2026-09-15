from trading_assistant.execution.preview import HumanOrderPreviewService


class Broker:
    def __init__(self, quote=1000.0, base=1.0):
        self.quote = quote
        self.base = base
        self.mutations = 0

    def market(self, symbol):
        return {
            "active": True, "base": "BTC", "quote": "USDT",
            "limits": {"amount": {"min": .001}, "cost": {"min": 5}},
        }

    def normalize_amount(self, symbol, quantity):
        return round(quantity, 3)

    def normalize_price(self, symbol, price):
        return round(price, 2)

    def fetch_ticker(self, symbol):
        return {"bid": 64990, "ask": 65010, "last": 65000}

    def fetch_balance(self):
        return {"free": {"BTC": self.base, "USDT": self.quote}}

    def create_order(self, *args, **kwargs):
        self.mutations += 1
        raise AssertionError("preview attempted mutation")


def test_buy_preview_is_approved_with_balance():
    broker = Broker()
    p = HumanOrderPreviewService(broker).preview(
        "BTC/USDT", "buy", "market", .001
    )
    assert p.approved
    assert p.estimated_price == 65010
    assert broker.mutations == 0


def test_buy_preview_blocks_insufficient_quote_balance():
    p = HumanOrderPreviewService(Broker(quote=1)).preview(
        "BTC/USDT", "buy", "market", .001
    )
    assert not p.approved
    assert "insufficient quote balance" in p.blockers


def test_sell_preview_blocks_insufficient_base_balance():
    p = HumanOrderPreviewService(Broker(base=0)).preview(
        "BTC/USDT", "sell", "market", .001
    )
    assert not p.approved
    assert "insufficient base balance" in p.blockers


def test_precision_is_applied_before_validation():
    p = HumanOrderPreviewService(Broker()).preview(
        "BTC/USDT", "buy", "limit", .0014, limit_price=65000.129
    )
    assert p.quantity == .001
    assert p.estimated_price == 65000.13
