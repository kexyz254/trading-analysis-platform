from trading_assistant.agents.alerting import AlertManager
from trading_assistant.agents.circuit_breaker import CircuitBreaker
from trading_assistant.persistence.journal import Journal


def test_trips_after_max_consecutive_errors():
    breaker = CircuitBreaker(max_consecutive_errors=3)

    breaker.record_result("BTC/USDT", success=False)
    breaker.record_result("BTC/USDT", success=False)
    assert breaker.tripped is False

    breaker.record_result("BTC/USDT", success=False)
    assert breaker.tripped is True
    assert "3 consecutive execution errors" in breaker.trip_reason
    assert "BTC/USDT" in breaker.trip_reason


def test_success_resets_the_consecutive_error_counter():
    breaker = CircuitBreaker(max_consecutive_errors=3)

    breaker.record_result("BTC/USDT", success=False)
    breaker.record_result("BTC/USDT", success=False)
    breaker.record_result("BTC/USDT", success=True)
    breaker.record_result("BTC/USDT", success=False)
    breaker.record_result("BTC/USDT", success=False)

    assert breaker.tripped is False  # never reached 3 in a row


def test_consecutive_errors_are_tracked_independently_per_symbol():
    """A shared counter would let one symbol's success reset another
    symbol's (or its own alternating) failures, so a permanently broken
    symbol could never trip the breaker."""
    breaker = CircuitBreaker(max_consecutive_errors=3)

    breaker.record_result("BTC/USDT", success=False)
    breaker.record_result("BTC/USDT", success=False)
    breaker.record_result("ETH/USDT", success=True)
    breaker.record_result("BTC/USDT", success=False)

    assert breaker.tripped is True


def test_manual_trip_and_reset():
    breaker = CircuitBreaker()

    breaker.trip("daily loss limit breached")
    assert breaker.tripped is True
    assert breaker.trip_reason == "daily loss limit breached"

    breaker.reset(operator="alice")
    assert breaker.tripped is False
    assert breaker.trip_reason is None


def test_trip_state_persists_across_process_restarts(tmp_path):
    """A tripped breaker must not silently clear itself just because the
    process restarted - that would defeat the point of a kill switch."""
    journal = Journal(db_path=str(tmp_path / "journal.db"))
    breaker = CircuitBreaker(journal=journal)
    breaker.trip("simulated serious failure")

    # Simulate a fresh process: a brand-new CircuitBreaker reading the same journal.
    restarted_breaker = CircuitBreaker(journal=journal)

    assert restarted_breaker.tripped is True
    assert restarted_breaker.trip_reason == "simulated serious failure"


def test_reset_persists_and_a_fresh_instance_starts_clear(tmp_path):
    journal = Journal(db_path=str(tmp_path / "journal.db"))
    breaker = CircuitBreaker(journal=journal)
    breaker.trip("simulated failure")
    breaker.reset(operator="ops")

    restarted_breaker = CircuitBreaker(journal=journal)

    assert restarted_breaker.tripped is False


def test_double_trip_does_not_overwrite_original_reason():
    breaker = CircuitBreaker()
    breaker.trip("first reason")
    breaker.trip("second reason")

    assert breaker.trip_reason == "first reason"


def test_record_result_works_normally_after_a_reset():
    """Regression: reset() used to set _consecutive_errors to an int (0)
    instead of an empty dict, so the very next record_result call after
    ANY manual reset crashed with AttributeError ('int' object has no
    attribute 'get') - silently breaking error tracking for the rest of
    the process's life, since Orchestrator's per-symbol try/except would
    swallow the crash every cycle without ever fixing the underlying state."""
    breaker = CircuitBreaker(max_consecutive_errors=3)
    breaker.trip("first incident")
    breaker.reset(operator="ops")

    # Must not raise, and must still count fresh errors correctly post-reset.
    breaker.record_result("BTC/USDT", success=False)
    breaker.record_result("BTC/USDT", success=False)
    assert breaker.tripped is False
    breaker.record_result("BTC/USDT", success=False)
    assert breaker.tripped is True


def test_trip_raises_an_alert_when_an_alert_manager_is_configured(tmp_path):
    journal = Journal(db_path=str(tmp_path / "journal.db"))
    alert_manager = AlertManager(journal=journal)
    breaker = CircuitBreaker(alert_manager=alert_manager)

    breaker.trip("simulated serious failure")

    alerts = journal.recent_alerts()
    assert len(alerts) == 1
    assert alerts[0]["category"] == "circuit_breaker"
    assert alerts[0]["severity"] == "critical"
    assert "simulated serious failure" in alerts[0]["message"]


def test_no_alert_manager_does_not_raise():
    breaker = CircuitBreaker()
    breaker.trip("simulated serious failure")  # must not raise
