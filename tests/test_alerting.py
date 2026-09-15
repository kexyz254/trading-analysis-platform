import pytest

from trading_assistant.agents.alerting import Alert, AlertManager, build_alert_email_html, build_alert_email_text
from trading_assistant.persistence.journal import Journal


def test_raise_alert_returns_alert_with_given_fields():
    manager = AlertManager()

    alert = manager.raise_alert("circuit_breaker", "critical", "breaker tripped", context={"reason": "x"})

    assert alert.category == "circuit_breaker"
    assert alert.severity == "critical"
    assert alert.message == "breaker tripped"
    assert alert.context == {"reason": "x"}


def test_invalid_severity_raises_value_error():
    manager = AlertManager()

    with pytest.raises(ValueError):
        manager.raise_alert("category", "not_a_real_severity", "message")


def test_invalid_email_min_severity_fails_fast_at_construction():
    with pytest.raises(ValueError):
        AlertManager(email_min_severity="not_a_real_severity")


def test_alert_persisted_to_journal(tmp_path):
    journal = Journal(db_path=str(tmp_path / "journal.db"))
    manager = AlertManager(journal=journal)

    manager.raise_alert("drift", "warning", "feature drift detected", context={"symbol": "BTC/USDT"})

    alerts = journal.recent_alerts()
    assert len(alerts) == 1
    assert alerts[0]["category"] == "drift"
    assert alerts[0]["severity"] == "warning"
    assert alerts[0]["context"] == {"symbol": "BTC/USDT"}


def test_no_journal_does_not_raise():
    manager = AlertManager(journal=None)

    manager.raise_alert("category", "info", "message")  # must not raise


def test_recent_alerts_filters_by_severity(tmp_path):
    journal = Journal(db_path=str(tmp_path / "journal.db"))
    manager = AlertManager(journal=journal)

    manager.raise_alert("a", "info", "info message")
    manager.raise_alert("b", "critical", "critical message")

    critical_only = journal.recent_alerts(severity="critical")
    assert len(critical_only) == 1
    assert critical_only[0]["category"] == "b"


def test_webhook_delivery_attempted_when_configured(monkeypatch):
    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append((url, json, timeout))

        class FakeResponse:
            status_code = 200

        return FakeResponse()

    monkeypatch.setattr("trading_assistant.agents.alerting.requests.post", fake_post)
    manager = AlertManager(webhook_url="https://example.invalid/webhook")

    manager.raise_alert("circuit_breaker", "critical", "breaker tripped")
    manager.wait_for_pending_alerts()

    assert len(calls) == 1
    url, payload, timeout = calls[0]
    assert url == "https://example.invalid/webhook"
    assert "breaker tripped" in payload["text"]
    assert timeout == 5.0


def test_webhook_not_called_when_not_configured(monkeypatch):
    calls = []
    monkeypatch.setattr("trading_assistant.agents.alerting.requests.post", lambda *a, **kw: calls.append(1))
    manager = AlertManager(webhook_url=None)

    manager.raise_alert("category", "info", "message")

    assert calls == []


def test_webhook_failure_does_not_raise(monkeypatch):
    def raising_post(*args, **kwargs):
        raise ConnectionError("network down")

    monkeypatch.setattr("trading_assistant.agents.alerting.requests.post", raising_post)
    manager = AlertManager(webhook_url="https://example.invalid/webhook")

    # Must not raise, even though the webhook call itself fails.
    alert = manager.raise_alert("category", "critical", "message")
    assert alert.severity == "critical"


def test_webhook_failure_still_persists_alert_to_journal(tmp_path, monkeypatch):
    def raising_post(*args, **kwargs):
        raise ConnectionError("network down")

    monkeypatch.setattr("trading_assistant.agents.alerting.requests.post", raising_post)
    journal = Journal(db_path=str(tmp_path / "journal.db"))
    manager = AlertManager(journal=journal, webhook_url="https://example.invalid/webhook")

    manager.raise_alert("category", "critical", "message")

    assert len(journal.recent_alerts()) == 1


def test_dedupe_key_suppresses_repeat_webhook_within_cooldown(monkeypatch):
    calls = []
    monkeypatch.setattr("trading_assistant.agents.alerting.requests.post", lambda *a, **kw: calls.append(1))
    manager = AlertManager(webhook_url="https://example.invalid/webhook", webhook_cooldown_seconds=3600.0)

    manager.raise_alert("drift", "warning", "first", dedupe_key="drift:BTC/USDT")
    manager.raise_alert("drift", "warning", "second", dedupe_key="drift:BTC/USDT")
    manager.wait_for_pending_alerts()

    assert len(calls) == 1  # second suppressed - still within cooldown


def test_dedupe_key_still_persists_every_occurrence_to_journal(tmp_path, monkeypatch):
    monkeypatch.setattr("trading_assistant.agents.alerting.requests.post", lambda *a, **kw: None)
    journal = Journal(db_path=str(tmp_path / "journal.db"))
    manager = AlertManager(journal=journal, webhook_url="https://example.invalid/webhook")

    manager.raise_alert("drift", "warning", "first", dedupe_key="drift:BTC/USDT")
    manager.raise_alert("drift", "warning", "second", dedupe_key="drift:BTC/USDT")

    # The webhook is rate-limited, but the audit trail must never drop an occurrence.
    assert len(journal.recent_alerts()) == 2


def test_different_dedupe_keys_are_independent(monkeypatch):
    calls = []
    monkeypatch.setattr("trading_assistant.agents.alerting.requests.post", lambda *a, **kw: calls.append(1))
    manager = AlertManager(webhook_url="https://example.invalid/webhook")

    manager.raise_alert("drift", "warning", "btc drift", dedupe_key="drift:BTC/USDT")
    manager.raise_alert("drift", "warning", "eth drift", dedupe_key="drift:ETH/USDT")
    manager.wait_for_pending_alerts()

    assert len(calls) == 2  # different keys, both send


def test_cooldown_expiry_allows_a_fresh_webhook(monkeypatch):
    calls = []
    monkeypatch.setattr("trading_assistant.agents.alerting.requests.post", lambda *a, **kw: calls.append(1))
    fake_time = [1000.0]
    monkeypatch.setattr("trading_assistant.agents.alerting.time.monotonic", lambda: fake_time[0])
    manager = AlertManager(webhook_url="https://example.invalid/webhook", webhook_cooldown_seconds=60.0)

    manager.raise_alert("drift", "warning", "first", dedupe_key="drift:BTC/USDT")
    fake_time[0] += 61.0
    manager.raise_alert("drift", "warning", "second", dedupe_key="drift:BTC/USDT")
    manager.wait_for_pending_alerts()

    assert len(calls) == 2  # cooldown expired between calls


def test_dedupe_cooldown_survives_a_restart_when_journal_is_configured(tmp_path, monkeypatch):
    """The core fix: dedup used to live only in an in-memory dict, so a
    crash-restart loop (scripts/run_forever.sh retries every 15s) reset
    the cooldown on every attempt - a persistent failure could spam a
    webhook once per restart instead of respecting cooldown_seconds
    across the whole episode. A journal-backed AlertManager must suppress
    the repeat even across a brand-new instance sharing the same journal
    (simulating exactly that restart)."""
    calls = []
    monkeypatch.setattr("trading_assistant.agents.alerting.requests.post", lambda *a, **kw: calls.append(1))
    journal = Journal(db_path=str(tmp_path / "journal.db"))
    first_process_manager = AlertManager(
        journal=journal, webhook_url="https://example.invalid/webhook", webhook_cooldown_seconds=3600.0
    )
    first_process_manager.raise_alert("drift", "warning", "first", dedupe_key="drift:BTC/USDT")
    first_process_manager.wait_for_pending_alerts()

    # simulate a restart: a brand new AlertManager instance, fresh
    # in-memory state, same journal
    second_process_manager = AlertManager(
        journal=journal, webhook_url="https://example.invalid/webhook", webhook_cooldown_seconds=3600.0
    )
    second_process_manager.raise_alert("drift", "warning", "second", dedupe_key="drift:BTC/USDT")
    second_process_manager.wait_for_pending_alerts()

    assert len(calls) == 1  # second instance's send was still suppressed by the persisted cooldown


def test_dedupe_cooldown_without_a_journal_does_not_survive_a_restart(monkeypatch):
    """Backward compatible: no journal configured falls back to the
    original in-memory-only behavior - a fresh instance has no way to
    know about a prior process's sends."""
    calls = []
    monkeypatch.setattr("trading_assistant.agents.alerting.requests.post", lambda *a, **kw: calls.append(1))
    first = AlertManager(webhook_url="https://example.invalid/webhook", webhook_cooldown_seconds=3600.0)
    first.raise_alert("drift", "warning", "first", dedupe_key="drift:BTC/USDT")
    first.wait_for_pending_alerts()

    second = AlertManager(webhook_url="https://example.invalid/webhook", webhook_cooldown_seconds=3600.0)
    second.raise_alert("drift", "warning", "second", dedupe_key="drift:BTC/USDT")
    second.wait_for_pending_alerts()

    assert len(calls) == 2  # no shared journal - each instance's cooldown is independent


def test_alert_email_html_shows_plain_language_title_and_raw_technical_message():
    # A known category gets a human-friendly translation ON TOP of, not
    # instead of, the exact technical message - both must be present so
    # nothing is hidden from someone who does understand trading.
    alert = Alert(category="circuit_breaker", severity="critical", message="circuit breaker tripped: too many errors")

    rendered = build_alert_email_html(alert)

    assert "Trading Has Been Paused" in rendered
    assert "circuit breaker tripped: too many errors" in rendered


def test_alert_email_html_falls_back_to_generic_title_for_unknown_category():
    alert = Alert(category="some_future_category_not_yet_mapped", severity="warning", message="something happened")

    rendered = build_alert_email_html(alert)

    assert "System Alert" in rendered
    assert "something happened" in rendered


def test_alert_email_html_position_unprotected_category_reads_as_needing_action():
    alert = Alert(
        category="position_unprotected", severity="critical",
        message="BTC/USDT: a real position is currently open with NO protective stop-loss",
    )

    rendered = build_alert_email_html(alert)

    assert "No Safety Net" in rendered
    assert "Yes, action needed" in rendered


def test_alert_email_html_renders_context_as_a_details_table():
    alert = Alert(
        category="feature_drift", severity="critical", message="BTC/USDT drifted",
        context={"symbol": "BTC/USDT", "worst_feature_psi": 1.76},
    )

    rendered = build_alert_email_html(alert)

    assert "BTC/USDT" in rendered
    assert "1.76" in rendered
    assert "Technical details" in rendered


def test_alert_email_html_omits_details_table_when_no_context():
    alert = Alert(category="circuit_breaker", severity="critical", message="tripped")

    rendered = build_alert_email_html(alert)

    assert "Technical details" not in rendered


def test_alert_email_html_escapes_message_and_context_content():
    # alert.message/context are internal-code-generated in practice, but
    # this is still the one place their content gets embedded into raw
    # HTML - escaping is verified explicitly rather than assumed.
    alert = Alert(
        category="circuit_breaker", severity="critical", message="<script>alert(1)</script>",
        context={"payload": "<img src=x onerror=alert(1)>"},
    )

    rendered = build_alert_email_html(alert)

    assert "<script>" not in rendered
    assert "<img" not in rendered
    assert "&lt;script&gt;" in rendered


def test_alert_email_text_contains_title_explanation_and_message():
    alert = Alert(category="model_decay", severity="critical", message="BTC/USDT: live win rate decayed")

    rendered = build_alert_email_text(alert)

    assert "A Trading Model Is Underperforming Live" in rendered
    assert "BTC/USDT: live win rate decayed" in rendered


def test_alert_email_html_has_the_four_structured_teaching_sections():
    # The whole point of the structured redesign - a wall-of-text single
    # paragraph was tried first and explicitly rejected as not educational
    # enough, so each of these four labeled sections must actually appear,
    # each with real (non-empty) content, not just the section label.
    alert = Alert(category="circuit_breaker", severity="critical", message="tripped")

    rendered = build_alert_email_html(alert)

    assert "What happened" in rendered
    assert "detected a serious problem" in rendered
    assert "Why it matters" in rendered
    assert "unnecessary risk" in rendered
    assert "What the system did" in rendered
    assert "Paused all trading" in rendered
    assert "Do you need to act?" in rendered
    assert "trading stays paused until a person reviews" in rendered


def test_alert_email_html_action_callout_reflects_whether_action_is_needed():
    needs_action = build_alert_email_html(Alert(category="circuit_breaker", severity="critical", message="x"))
    no_action = build_alert_email_html(Alert(category="stop_loss_triggered", severity="warning", message="x"))
    unknown = build_alert_email_html(Alert(category="totally_unmapped_category", severity="info", message="x"))

    assert "Yes, action needed" in needs_action
    assert "No action needed" in no_action
    assert "Worth a look" in unknown


def test_alert_email_text_includes_all_four_sections_in_order():
    alert = Alert(category="feature_drift", severity="critical", message="BTC/USDT drifted")

    rendered = build_alert_email_text(alert)

    assert rendered.index("WHAT HAPPENED") < rendered.index("WHY IT MATTERS")
    assert rendered.index("WHY IT MATTERS") < rendered.index("WHAT THE SYSTEM DID")
    assert rendered.index("WHAT THE SYSTEM DID") < rendered.index("DO YOU NEED TO ACT")


def test_every_mapped_category_has_all_required_fields():
    # Cheap but effective regression guard against a typo'd/missing key in
    # any one category's dict silently producing a broken card for that
    # specific alert type only (which would be easy to miss, since most
    # manual testing only exercises 1-2 categories at a time).
    from trading_assistant.agents.alerting import _CATEGORY_INFO, _DEFAULT_CATEGORY_INFO

    required_keys = {"icon", "title", "what_happened", "why_it_matters", "system_action", "needs_action", "action_detail"}
    for category, info in {**_CATEGORY_INFO, "__default__": _DEFAULT_CATEGORY_INFO}.items():
        missing = required_keys - info.keys()
        assert not missing, f"{category} is missing fields: {missing}"
        assert info["needs_action"] in (True, False, None)
        # Every category should at least build without raising, for both severities email actually uses.
        for severity in ("critical", "warning"):
            build_alert_email_html(Alert(category=category, severity=severity, message="x"))


class FakeSMTP:
    """Hand-rolled fake matching smtplib.SMTP's context-manager/login/
    send_message interface, in the same spirit as fake_post above -
    records what was sent instead of touching a real network socket."""

    instances = []

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.started_tls = False
        self.login_calls = []
        self.sent_messages = []
        FakeSMTP.instances.append(self)

    def starttls(self):
        self.started_tls = True

    def login(self, username, password):
        self.login_calls.append((username, password))

    def send_message(self, email):
        self.sent_messages.append(email)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


@pytest.fixture(autouse=True)
def _reset_fake_smtp_instances():
    FakeSMTP.instances = []
    yield
    FakeSMTP.instances = []


def _email_manager(monkeypatch, **overrides):
    monkeypatch.setattr("trading_assistant.agents.alerting.smtplib.SMTP", FakeSMTP)
    kwargs = dict(
        smtp_username="bot@example.com",
        smtp_password="app-password",
        email_to=["ops@example.com"],
    )
    kwargs.update(overrides)
    return AlertManager(**kwargs)


def test_email_delivery_attempted_when_configured(monkeypatch):
    manager = _email_manager(monkeypatch)

    manager.raise_alert("circuit_breaker", "critical", "breaker tripped")
    manager.wait_for_pending_alerts()

    assert len(FakeSMTP.instances) == 1
    smtp = FakeSMTP.instances[0]
    assert smtp.host == "smtp.gmail.com"
    assert smtp.port == 587
    assert smtp.started_tls is True
    assert smtp.login_calls == [("bot@example.com", "app-password")]
    assert len(smtp.sent_messages) == 1
    sent = smtp.sent_messages[0]
    assert sent["To"] == "ops@example.com"
    assert "circuit_breaker" in sent["Subject"]


def test_email_is_multipart_with_both_plain_and_html_bodies(monkeypatch):
    # HTML-only would leave a client that can't render HTML with an empty
    # message - both parts must always be present.
    manager = _email_manager(monkeypatch)

    manager.raise_alert("circuit_breaker", "critical", "breaker tripped")
    manager.wait_for_pending_alerts()

    sent = FakeSMTP.instances[0].sent_messages[0]
    assert sent.get_content_type() == "multipart/alternative"
    text_body = sent.get_body(preferencelist=("plain",)).get_content()
    html_body = sent.get_body(preferencelist=("html",)).get_content()
    assert "breaker tripped" in text_body
    assert "breaker tripped" in html_body
    assert "Trading Has Been Paused" in html_body


def test_email_not_sent_when_not_configured(monkeypatch):
    monkeypatch.setattr("trading_assistant.agents.alerting.smtplib.SMTP", FakeSMTP)
    manager = AlertManager()  # no smtp credentials at all

    manager.raise_alert("category", "critical", "message")

    assert FakeSMTP.instances == []


def test_email_only_sent_for_severities_at_or_above_the_configured_minimum(monkeypatch):
    manager = _email_manager(monkeypatch, email_min_severity="critical")

    manager.raise_alert("drift", "warning", "not critical enough")
    manager.raise_alert("circuit_breaker", "critical", "critical enough")
    manager.wait_for_pending_alerts()

    assert len(FakeSMTP.instances) == 1
    assert "critical" in FakeSMTP.instances[0].sent_messages[0]["Subject"].lower()


def test_email_min_severity_can_be_lowered_to_include_warnings(monkeypatch):
    manager = _email_manager(monkeypatch, email_min_severity="warning")

    manager.raise_alert("drift", "warning", "now included")
    manager.wait_for_pending_alerts()

    assert len(FakeSMTP.instances) == 1


def test_email_failure_does_not_raise_and_still_persists_to_journal(tmp_path, monkeypatch):
    class RaisingSMTP(FakeSMTP):
        def __enter__(self):
            raise ConnectionError("smtp down")

    monkeypatch.setattr("trading_assistant.agents.alerting.smtplib.SMTP", RaisingSMTP)
    journal = Journal(db_path=str(tmp_path / "journal.db"))
    manager = AlertManager(
        journal=journal, smtp_username="bot@example.com", smtp_password="x", email_to=["ops@example.com"]
    )

    alert = manager.raise_alert("category", "critical", "message")  # must not raise

    assert alert.severity == "critical"
    assert len(journal.recent_alerts()) == 1


def test_email_dedupe_key_suppresses_repeat_within_cooldown(monkeypatch):
    manager = _email_manager(monkeypatch, email_cooldown_seconds=3600.0)

    manager.raise_alert("drift", "critical", "first", dedupe_key="drift:BTC/USDT")
    manager.raise_alert("drift", "critical", "second", dedupe_key="drift:BTC/USDT")
    manager.wait_for_pending_alerts()

    assert len(FakeSMTP.instances) == 1


def test_email_and_webhook_cooldowns_are_independent(monkeypatch):
    """Regression guard: throttling one channel must never silently
    throttle the other - they track separate dedupe state."""
    webhook_calls = []
    monkeypatch.setattr("trading_assistant.agents.alerting.requests.post", lambda *a, **kw: webhook_calls.append(1))
    manager = _email_manager(
        monkeypatch,
        webhook_url="https://example.invalid/webhook",
        webhook_cooldown_seconds=3600.0,
        email_cooldown_seconds=3600.0,
    )

    manager.raise_alert("drift", "critical", "first", dedupe_key="drift:BTC/USDT")
    # exhaust the email channel's cooldown for this key, then fire once more
    manager.raise_alert("drift", "critical", "second", dedupe_key="drift:BTC/USDT")
    manager.wait_for_pending_alerts()

    assert len(webhook_calls) == 1  # webhook suppressed on the repeat, same as before
    assert len(FakeSMTP.instances) == 1  # email suppressed on the repeat too, independently tracked


def test_email_from_defaults_to_smtp_username_when_not_set(monkeypatch):
    manager = _email_manager(monkeypatch, email_from=None)

    manager.raise_alert("category", "critical", "message")
    manager.wait_for_pending_alerts()

    assert FakeSMTP.instances[0].sent_messages[0]["From"] == "bot@example.com"


def test_raise_alert_does_not_block_on_slow_email_delivery(monkeypatch):
    """The actual regression this exists for: a real measured SMTP
    round-trip takes ~3 seconds, and raise_alert() is called deep inside
    the live per-symbol cycle loop - if it blocked on delivery, every
    critical alert would stall real trading evaluation for that long.
    Delivery must be dispatched in the background; raise_alert() itself
    must return almost immediately regardless of how slow the SMTP server
    on the other end is."""
    import time

    class SlowSMTP(FakeSMTP):
        def login(self, username, password):
            time.sleep(0.5)
            super().login(username, password)

    monkeypatch.setattr("trading_assistant.agents.alerting.smtplib.SMTP", SlowSMTP)
    manager = AlertManager(smtp_username="bot@example.com", smtp_password="x", email_to=["ops@example.com"])

    start = time.perf_counter()
    manager.raise_alert("circuit_breaker", "critical", "breaker tripped")
    elapsed = time.perf_counter() - start

    assert elapsed < 0.3  # returned well before the simulated 0.5s SMTP delay
    manager.wait_for_pending_alerts()
    assert len(FakeSMTP.instances) == 1  # the send still actually happened, just in the background


def test_no_dedupe_key_always_sends():
    """Circuit breaker trips (and similarly inherently one-off events)
    pass no dedupe_key - they should never be suppressed."""
    calls = []

    class Manager(AlertManager):
        def _send_webhook(self, alert):
            calls.append(alert)

    manager = Manager(webhook_url="https://example.invalid/webhook")

    manager.raise_alert("circuit_breaker", "critical", "tripped")
    manager.raise_alert("circuit_breaker", "critical", "tripped again")
    manager.wait_for_pending_alerts()

    assert len(calls) == 2

def test_failed_email_is_not_marked_delivered_and_can_retry(tmp_path, monkeypatch):
    class RaisingSMTP(FakeSMTP):
        def __enter__(self):
            raise ConnectionError("smtp down")

    journal = Journal(db_path=str(tmp_path / "journal.db"))
    monkeypatch.setattr("trading_assistant.agents.alerting.smtplib.SMTP", RaisingSMTP)

    manager = AlertManager(
        journal=journal,
        smtp_username="bot@example.com",
        smtp_password="x",
        email_to=["ops@example.com"],
    )

    key = "production:test"
    manager.raise_alert("category", "critical", "first", dedupe_key=key)
    manager.wait_for_pending_alerts()

    assert journal.last_alert_delivery(channel="email", dedupe_key=key) is None

    monkeypatch.setattr("trading_assistant.agents.alerting.smtplib.SMTP", FakeSMTP)
    manager.raise_alert("category", "critical", "retry", dedupe_key=key)
    manager.wait_for_pending_alerts()

    assert journal.last_alert_delivery(channel="email", dedupe_key=key) is not None


def test_force_email_allows_one_recovery_notification(monkeypatch):
    manager = _email_manager(monkeypatch)

    manager.raise_alert(
        "circuit_breaker_recovery",
        "info",
        "breaker reset",
        dedupe_key="breaker-recovery:incident-1",
        force_email=True,
    )
    manager.wait_for_pending_alerts()

    assert len(FakeSMTP.instances) == 1
