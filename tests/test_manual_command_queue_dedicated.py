import time

import pytest

from trading_assistant.execution.manual_command_queue import (
    ManualCommandQueue,
)


def queue(tmp_path):
    return ManualCommandQueue(
        str(tmp_path / "commands.db")
    )


def enqueue(q, request_id="request-123", ttl=60):
    return q.enqueue(
        request_id=request_id,
        expires_at=time.time() + ttl,
        account_id="primary",
        operator_id="owner",
        symbol="BTC/USDT",
        side="buy",
        order_type="market",
        quantity=.001,
        limit_price=None,
        provenance="MANUAL",
        confirmation_fingerprint="fingerprint",
    )


def test_enqueue_is_durable(tmp_path):
    q = queue(tmp_path)

    assert enqueue(q)

    reopened = queue(tmp_path)
    row = reopened.get("request-123")

    assert row["status"] == "PENDING"
    assert row["symbol"] == "BTC/USDT"


def test_duplicate_request_is_rejected(tmp_path):
    q = queue(tmp_path)

    assert enqueue(q)
    assert not enqueue(q)


def test_claim_is_atomic_one_shot(tmp_path):
    q = queue(tmp_path)

    enqueue(q)

    assert q.claim_next()["status"] == "CLAIMED"
    assert q.claim_next() is None


def test_terminal_completion_is_one_shot(tmp_path):
    q = queue(tmp_path)

    enqueue(q)
    q.claim_next()

    assert q.complete(
        "request-123",
        status="EXECUTED",
    )

    assert not q.complete(
        "request-123",
        status="EXECUTED",
    )


def test_expired_command_never_executes(tmp_path):
    q = queue(tmp_path)

    now = time.time()

    q.enqueue(
        request_id="expired",
        expires_at=now + 1,
        account_id="primary",
        operator_id="owner",
        symbol="BTC/USDT",
        side="buy",
        order_type="market",
        quantity=.001,
        limit_price=None,
        provenance="MANUAL",
        confirmation_fingerprint="fp",
    )

    assert q.claim_next(now=now + 2) is None

    row = q.get("expired")
    assert row["status"] == "REJECTED"


def test_stale_claim_becomes_uncertain_not_pending(tmp_path):
    q = queue(tmp_path)

    enqueue(q)
    claimed = q.claim_next(now=1000)

    assert claimed["status"] == "CLAIMED"

    recovered = q.recover_stale_claims(
        now=1040,
        max_claim_age_seconds=30,
    )

    assert recovered == 1

    row = q.get("request-123")

    assert row["status"] == "UNCERTAIN"
    assert q.claim_next(now=1041) is None


def test_non_human_provenance_is_rejected(tmp_path):
    q = queue(tmp_path)

    with pytest.raises(ValueError):
        q.enqueue(
            request_id="bad-provenance",
            expires_at=time.time() + 60,
            account_id="primary",
            operator_id="owner",
            symbol="BTC/USDT",
            side="buy",
            order_type="market",
            quantity=.001,
            limit_price=None,
            provenance="ATS_AUTONOMOUS",
            confirmation_fingerprint="fp",
        )
