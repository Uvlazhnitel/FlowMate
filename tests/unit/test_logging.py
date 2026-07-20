import json
import logging

from flowmate.core.logging import JsonFormatter


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
