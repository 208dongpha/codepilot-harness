import json
import logging
import sys

SAFE_EVENTS = {
    "application_startup",
    "configuration_error",
    "unexpected_exception",
    "docker_smoke_or_configuration_error",
    "docker_error unavailable_or_timeout",
    "docker_smoke PASS",
    "docker_smoke FAIL",
    "docker_cleanup FAIL",
    *(
        f"gemini_error {code}"
        for code in (
            "AUTHENTICATION_ERROR",
            "RATE_LIMIT_ERROR",
            "TIMEOUT_ERROR",
            "MODEL_ERROR",
            "PROVIDER_ERROR",
        )
    ),
}


class EventFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # Only known events are emitted; arbitrary content and tracebacks are omitted.
        message = record.getMessage()
        event = message if message in SAFE_EVENTS else "[REDACTED]"
        return json.dumps({"level": record.levelname, "event": event})


def configure_logging(level: str = "INFO") -> None:
    logger = logging.getLogger("codepilot")
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(EventFormatter())
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(level)
