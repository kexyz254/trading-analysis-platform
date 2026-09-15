import pytest

from trading_assistant.execution.human import (
    DuplicateOrderRequest, HumanExecutionService, HumanOrderCommand,
)
from trading_assistant.execution.provenance import TradeProvenance
from trading_assistant.persistence.journal import Journal


class Broker:
    def __init__(self):
        self.orders = []

    def market(self, symbol):
        return {"active": True, "limits": {"amount": {"min": .001}, "cost": {"min": 5}}}

    def submit_market_order(self, symbol, side, quantity, client_order_id):
        order = {"id": "1", "status": "closed", "amount": quantity}
        self.orders.append((symbol, side, quantity, client_order_id))
        return order

    def submit_limit_order(self, symbol, side, quantity, price, client_order_id):
        order = {"id": "2", "status": "open", "amount": quantity, "price": price}
        self.orders.append((symbol, side, quantity, client_order_id))
        return order


def cmd(**changes):
    values = dict(
        request_id="req-1", account_id="primary", operator_id="owner",
        symbol="BTC/USDT", side="buy", order_type="market",
        quantity=.001, reference_price=65000,
        provenance=TradeProvenance.MANUAL, confirmed=True,
    )
    values.update(changes)
    return HumanOrderCommand(**values)


def service(tmp_path):
    broker = Broker()
    journal = Journal(str(tmp_path / "journal.db"))
    return HumanExecutionService(broker, journal), broker, journal


def test_confirmed_order_is_audited(tmp_path):
    svc, broker, journal = service(tmp_path)
    receipt = svc.submit(cmd())

    assert len(broker.orders) == 1
    assert receipt.broker_client_order_id.startswith("atsm-")
    assert journal.manual_order_intent("req-1") is not None


def test_same_request_cannot_submit_twice(tmp_path):
    svc, broker, _ = service(tmp_path)

    svc.submit(cmd())
    with pytest.raises(DuplicateOrderRequest):
        svc.submit(cmd())

    assert len(broker.orders) == 1


def test_unconfirmed_never_claims_or_submits(tmp_path):
    svc, broker, journal = service(tmp_path)

    with pytest.raises(PermissionError):
        svc.submit(cmd(confirmed=False))

    assert broker.orders == []
    assert journal.manual_order_intent("req-1") is None


def test_autonomous_provenance_cannot_use_human_service(tmp_path):
    svc, broker, _ = service(tmp_path)

    with pytest.raises(PermissionError):
        svc.submit(cmd(provenance=TradeProvenance.ATS_AUTONOMOUS))

    assert broker.orders == []


def test_broker_minimum_blocks_before_claim(tmp_path):
    svc, broker, journal = service(tmp_path)

    with pytest.raises(ValueError):
        svc.submit(cmd(quantity=.00001))

    assert broker.orders == []
    assert journal.manual_order_intent("req-1") is None
