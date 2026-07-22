import json
import logging
import re
from datetime import UTC, datetime

REDACTION_PATTERNS = (
    re.compile(r"\b(?:sk|sess)-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)(?:postgresql(?:\+asyncpg)?://[^:\s]+:)[^@\s]+@"),
    re.compile(
        r"(?i)\b(?:OPENAI_API_KEY|TELEGRAM_BOT_TOKEN|APP_API_KEY|"
        r"PWA_AUTH_SECRET)\s*=\s*[^\s]+"
    ),
)


def redact_sensitive(value: str) -> str:
    sanitized = value
    for pattern in REDACTION_PATTERNS:
        sanitized = pattern.sub("[REDACTED]", sanitized)
    return sanitized


class SafeFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        return redact_sensitive(rendered)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_sensitive(record.getMessage()),
        }
        request_id = getattr(record, "request_id", None)
        if request_id is not None:
            payload["request_id"] = request_id
        if record.exc_info is not None:
            exception_type = record.exc_info[0]
            payload["exception"] = (
                exception_type.__name__ if exception_type is not None else "Exception"
            )
        return json.dumps(payload, ensure_ascii=True)


def configure_logging(level: str, *, structured: bool = False) -> None:
    handler = logging.StreamHandler()
    if structured:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            SafeFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level)

    for logger_name in ("httpcore", "httpx", "openai"):
        logging.getLogger(logger_name).setLevel(logging.WARNING)
