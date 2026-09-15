import pytest

from trading_assistant.execution.ownership import (
    OrderOwnership,
    OwnershipService,
)
from trading_assistant.execution.provenance import TradeProvenance
from trading_assistant.persistence.journal import Journal


def service(tmp_path):
    journal = Journal(str(tmp_path / "journal.db"))
    return OwnershipService(journal), journal


def test_manual_order_ownership_is_durable(tmp_path):
    svc, journal = service(tmp_path)

    svc.record_order(
        OrderOwnership(
            account_id="primary",
            symbol="BTC/USDT",
            side="buy",
            quantity=.001,
            provenance=TradeProvenance.MANUAL,
            broker_order_id="123",
            broker_client_order_id="atsm-test",
            request_id="request-123",
            operator_id="owner",
        )
    )

    row = journal.execution_ownership_for_order(
        "primary", "123"
    )

    assert row["provenance"] == "MANUAL"
    assert row["operator_id"] == "owner"


def test_autonomous_and_manual_are_distinct(tmp_path):
    svc, journal = service(tmp_path)

    svc.record_order(
        OrderOwnership(
            account_id="primary",
            symbol="ETH/USDT",
            side="buy",
            quantity=.1,
            provenance=TradeProvenance.ATS_AUTONOMOUS,
            broker_order_id="auto-1",
            strategy_id="champion-v7",
        )
    )

    row = journal.execution_ownership_for_order(
        "primary", "auto-1"
    )

    assert row["provenance"] == "ATS_AUTONOMOUS"
    assert row["operator_id"] is None
    assert row["strategy_id"] == "champion-v7"


def test_fill_preserves_position_provenance(tmp_path):
    svc, journal = service(tmp_path)

    svc.record_fill(
        account_id="primary",
        symbol="BTC/USDT",
        provenance=TradeProvenance.ATS_ASSISTED_MANUAL,
        side="buy",
        quantity=.001,
        price=77000,
        broker_order_id="1",
        request_id="r1",
    )

    row = journal.latest_position_ownership(
        "primary", "BTC/USDT"
    )

    assert row["provenance"] == "ATS_ASSISTED_MANUAL"
    assert row["event_type"] == "POSITION_INCREASE"


def test_zero_quantity_ownership_is_rejected(tmp_path):
    svc, _ = service(tmp_path)

    with pytest.raises(ValueError):
        svc.record_order(
            OrderOwnership(
                account_id="primary",
                symbol="BTC/USDT",
                side="buy",
                quantity=0,
                provenance=TradeProvenance.MANUAL,
            )
        )
