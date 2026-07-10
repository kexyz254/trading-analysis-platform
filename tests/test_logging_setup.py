import calendar
import logging
import time

from trading_assistant.logging_setup import JsonFormatter


def test_json_formatter_uses_explicit_nairobi_time_not_os_local_time():
    """Regression: formatTime used to rely on the base Formatter's default
    (the OS's local clock via time.localtime), which is only correct if the
    host machine happens to be configured to Africa/Nairobi - fragile and
    silently wrong on any other machine. Must be an explicit, guaranteed EAT
    conversion from record.created (a UTC-based epoch timestamp) instead."""
    formatter = JsonFormatter()
    # A known UTC instant: 2026-01-01T00:00:00Z == 2026-01-01T03:00:00+03:00 in Nairobi.
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0, msg="hello", args=(), exc_info=None
    )
    record.created = calendar.timegm(time.strptime("2026-01-01T00:00:00", "%Y-%m-%dT%H:%M:%S"))

    formatted = formatter.formatTime(record)

    assert formatted.startswith("2026-01-01T03:00:00")
    assert formatted.endswith("+0300")


def test_json_formatter_output_is_valid_json_with_expected_fields():
    import json

    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="my.logger", level=logging.WARNING, pathname="", lineno=0, msg="something happened", args=(), exc_info=None
    )

    payload = json.loads(formatter.format(record))

    assert payload["level"] == "WARNING"
    assert payload["logger"] == "my.logger"
    assert payload["message"] == "something happened"
    assert payload["timestamp"].endswith("+0300")
