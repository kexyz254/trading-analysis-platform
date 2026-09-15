from types import SimpleNamespace

from webapp.manual_trading import (
    SubmitOrderRequest,
    _confirmation_matches,
)


def request(**changes):
    values = dict(
        confirmation_token="x" * 40,
        request_id="request-123",
        symbol="BTC/USDT",
        side="buy",
        order_type="limit",
        quantity=.001,
        limit_price=77000,
    )
    values.update(changes)
    return SubmitOrderRequest(**values)


def confirmed(**changes):
    values = dict(
        operator_id="owner",
        request_id="request-123",
        symbol="BTC/USDT",
        side="buy",
        order_type="limit",
        quantity=.001,
        limit_price=77000.0,
    )
    values.update(changes)
    return SimpleNamespace(**values)


def test_exact_order_matches():
    assert _confirmation_matches(
        confirmed(), "owner", request(), .001, 77000.0
    )


def test_changed_quantity_is_rejected():
    assert not _confirmation_matches(
        confirmed(), "owner", request(quantity=.002), .002, 77000.0
    )


def test_changed_operator_is_rejected():
    assert not _confirmation_matches(
        confirmed(), "other-user", request(), .001, 77000.0
    )


def test_changed_price_is_rejected():
    assert not _confirmation_matches(
        confirmed(), "owner", request(limit_price=77100), .001, 77100.0
    )
