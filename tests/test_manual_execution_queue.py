from datetime import datetime, timedelta, timezone

from trading_assistant.persistence.journal import Journal


def iso(seconds):
    return (
        datetime.now(timezone.utc) + timedelta(seconds=seconds)
    ).isoformat()


def enqueue(journal, request_id="request-123", expires=60):
    return journal.enqueue_manual_execution_command(
        request_id=request_id,
        expires_at=iso(expires),
        account_id="primary",
        operator_id="owner",
        symbol="BTC/USDT",
        side="buy",
        order_type="market",
        quantity=.001,
        limit_price=None,
        provenance="MANUAL",
        confirmation_fingerprint="abc123",
    )


def test_command_is_durable(tmp_path):
    journal = Journal(str(tmp_path / "db.sqlite"))

    assert enqueue(journal)

    row = journal.manual_execution_command("request-123")

    assert row["status"] == "PENDING"
    assert row["provenance"] == "MANUAL"


def test_duplicate_request_id_cannot_enqueue_twice(tmp_path):
    journal = Journal(str(tmp_path / "db.sqlite"))

    assert enqueue(journal)
    assert not enqueue(journal)


def test_claim_is_one_shot(tmp_path):
    journal = Journal(str(tmp_path / "db.sqlite"))

    enqueue(journal)

    first = journal.claim_next_manual_execution_command()
    second = journal.claim_next_manual_execution_command()

    assert first["request_id"] == "request-123"
    assert first["status"] == "CLAIMED"
    assert second is None


def test_expired_command_is_never_claimed(tmp_path):
    journal = Journal(str(tmp_path / "db.sqlite"))

    enqueue(journal, expires=-60)

    assert journal.claim_next_manual_execution_command() is None


def test_claimed_command_can_complete_once(tmp_path):
    journal = Journal(str(tmp_path / "db.sqlite"))

    enqueue(journal)
    journal.claim_next_manual_execution_command()

    assert journal.complete_manual_execution_command(
        "request-123",
        status="EXECUTED",
        details={"broker_order_id": "1"},
    )

    assert not journal.complete_manual_execution_command(
        "request-123",
        status="EXECUTED",
    )

    row = journal.manual_execution_command("request-123")
    assert row["status"] == "EXECUTED"
