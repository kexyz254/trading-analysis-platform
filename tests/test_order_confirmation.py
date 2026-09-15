import time

import pytest

from webapp.order_confirmation import (
    ConfirmationError,
    OrderConfirmationService,
)


def service(ttl=60):
    return OrderConfirmationService(
        "test-confirmation-secret",
        ttl_seconds=ttl,
    )


def order(**changes):
    data = dict(
        operator_id="owner",
        request_id="req-123",
        symbol="BTC/USDT",
        side="buy",
        order_type="market",
        quantity=.001,
        limit_price=None,
    )
    data.update(changes)
    return data


def test_issue_and_verify_round_trip():
    svc = service()

    token = svc.issue(**order())
    confirmed = svc.verify(token)

    assert confirmed.operator_id == "owner"
    assert confirmed.request_id == "req-123"
    assert confirmed.symbol == "BTC/USDT"
    assert confirmed.quantity == .001


def test_token_is_tamper_evident():
    svc = service()

    token = svc.issue(**order())
    body, sig = token.split(".", 1)

    replacement = (
        ("A" if body[-1] != "A" else "B")
    )

    tampered = body[:-1] + replacement + "." + sig

    with pytest.raises(ConfirmationError):
        svc.verify(tampered)


def test_different_order_has_different_fingerprint():
    a = service().fingerprint(**order(quantity=.001))
    b = service().fingerprint(**order(quantity=.002))

    assert a != b


def test_operator_is_bound_into_fingerprint():
    a = service().fingerprint(**order(operator_id="owner"))
    b = service().fingerprint(**order(operator_id="someone-else"))

    assert a != b


def test_expired_token_is_rejected(monkeypatch):
    svc = service(ttl=1)

    monkeypatch.setattr(time, "time", lambda: 100)
    token = svc.issue(**order())

    monkeypatch.setattr(time, "time", lambda: 102)

    with pytest.raises(ConfirmationError):
        svc.verify(token)
