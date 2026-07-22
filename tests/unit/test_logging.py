import json
import logging

from flowmate.core.logging import JsonFormatter, configure_logging, redact_sensitive


def test_json_formatter_produces_structured_request_log() -> None:
    record = logging.LogRecord(
        name="flowmate.api",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="request_complete",
        args=(),
        exc_info=None,
    )
    record.request_id = "test-request-id"

    payload = json.loads(JsonFormatter().format(record))

    assert payload["timestamp"]
    assert payload["level"] == "INFO"
    assert payload["logger"] == "flowmate.api"
    assert payload["message"] == "request_complete"
    assert payload["request_id"] == "test-request-id"


def test_external_http_loggers_are_limited_to_safe_levels() -> None:
    configure_logging("DEBUG")

    assert logging.getLogger("openai").level == logging.WARNING
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING


def test_sensitive_values_are_redacted() -> None:
    rendered = redact_sensitive(
        "Bearer abcdefghijklmnop sk-exampleSecret123456 "
        "123456789:telegramTokenExample1234567890 "
        "postgresql+asyncpg://user:private-password@db/flowmate"
    )

    assert "private-password" not in rendered
    assert "exampleSecret" not in rendered
    assert "telegramToken" not in rendered
    assert rendered.count("[REDACTED]") == 4
