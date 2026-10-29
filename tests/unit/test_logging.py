import json
import logging

from codepilot.telemetry.logging import configure_logging


def test_structured_logging(capsys):
    configure_logging()
    logging.getLogger("codepilot").info("application_startup")
    record = json.loads(capsys.readouterr().err)
    assert record == {"level": "INFO", "event": "application_startup"}


def test_unknown_event_and_traceback_redacted():
    from codepilot.telemetry.logging import EventFormatter

    marker = "synthetic-sensitive-value"
    record = logging.LogRecord(
        "codepilot", logging.ERROR, "", 0, "Authorization: Bearer %s", (marker,), None
    )
    assert marker not in EventFormatter().format(record)
    assert json.loads(EventFormatter().format(record))["event"] == "[REDACTED]"
