import pytest

from trading_assistant.execution.human import (
    HumanExecutionService,
    HumanOrderCommand,
)
from trading_assistant.execution.human_gateway import HumanOrderGateway
from trading_assistant.execution.human_risk import HumanRiskPolicy
from trading_assistant.execution.preview import HumanOrderPreviewService
from trading_assistant.execution.provenance import TradeProvenance
from trading_assistant.persistence.journal import Journal


class Broker:
    def __init__(self, quote=1000, ask=100, bid=99.9):
        self.quote = quote
        self.ask = ask
        self.bid = bid
        self.orders = []

    def market(self, symbol):
        return {
            "active": True,
            "base": "AAA",
            "quote": "USDT",
            "limits": {
                "amount": {"min": .01},
                "cost": {"min": 5},
            },
        }

    def normalize_amount(self, symbol, quantity):
        return round(quantity, 2)

    def normalize_price(self, symbol, price):
        return round(price, 2)

    def fetch_ticker(self, symbol):
        return {
            "bid": self.bid,
            "ask": self.ask,
            "last": (self.bid + self.ask) / 2,
        }

    def fetch_balance(self):
        return {
            "free": {
                "AAA": 10,
                "USDT": self.quote,
            }
        }

    def submit_market_order(self, symbol, side, quantity, client_order_id):
        self.orders.append((symbol, side, quantity, client_order_id))
        return {
            "id": "1",
            "status": "closed",
            "amount": quantity,
        }

    def submit_limit_order(self, symbol, side, quantity, price, client_order_id):
        self.orders.append((symbol, side, quantity, client_order_id))
        return {
            "id": "2",
            "status": "open",
            "amount": quantity,
            "price": price,
        }


def command(quantity=1):
    return HumanOrderCommand(
        request_id="r1",
        account_id="primary",
        operator_id="owner",
        symbol="AAA/USDT",
        side="buy",
        order_type="market",
        quantity=quantity,
        reference_price=100,
        provenance=TradeProvenance.MANUAL,
        confirmed=True,
    )


def gateway(tmp_path, broker, policy=None):
    journal = Journal(str(tmp_path / "journal.db"))
    return HumanOrderGateway(
        HumanOrderPreviewService(broker),
        HumanExecutionService(broker, journal),
        policy or HumanRiskPolicy(),
    )


def test_prepare_never_submits(tmp_path):
    broker = Broker()
    prepared = gateway(tmp_path, broker).prepare(
        symbol="AAA/USDT",
        side="buy",
        order_type="market",
        quantity=1,
    )

    assert prepared.risk.approved
    assert broker.orders == []


def test_submit_rechecks_and_executes_once(tmp_path):
    broker = Broker()
    g = gateway(tmp_path, broker)

    g.submit(command())

    assert len(broker.orders) == 1


def test_balance_limit_blocks_before_broker_mutation(tmp_path):
    broker = Broker()
    policy = HumanRiskPolicy(max_balance_usage_pct=.05)

    g = gateway(tmp_path, broker, policy)

    with pytest.raises(PermissionError):
        g.submit(command(quantity=1))

    assert broker.orders == []


def test_max_notional_blocks(tmp_path):
    broker = Broker()
    g = gateway(
        tmp_path,
        broker,
        HumanRiskPolicy(max_order_notional=50),
    )

    prepared = g.prepare(
        symbol="AAA/USDT",
        side="buy",
        order_type="market",
        quantity=1,
    )

    assert not prepared.risk.approved
    assert "order exceeds configured maximum notional" in prepared.risk.blockers


def test_wide_spread_can_be_blocked(tmp_path):
    broker = Broker(ask=105, bid=95)

    g = gateway(
        tmp_path,
        broker,
        HumanRiskPolicy(max_spread_pct=.01),
    )

    prepared = g.prepare(
        symbol="AAA/USDT",
        side="buy",
        order_type="market",
        quantity=1,
    )

    assert not prepared.risk.approved
    assert "current spread exceeds configured limit" in prepared.risk.blockers
