from codepilot.tools.workspace import SECRET_CONTENT

REDACTED = "[REDACTED]"
TRUNCATION_MARKER = "\n[OUTPUT_TRUNCATED]"


def redact(text: str) -> str:
    return SECRET_CONTENT.sub(REDACTED, text)


def bound_and_redact(
    text: str, limit: int, marker: str = TRUNCATION_MARKER
) -> tuple[str, bool, bool]:
    """Return bounded, credential-redacted text plus truncation/redaction flags."""
    if limit < len(marker) + 1:
        raise ValueError("Output limit is too small.")
    cleaned = redact(text)
    redacted = cleaned != text
    if len(cleaned) <= limit:
        return cleaned, False, redacted
    return cleaned[: limit - len(marker)] + marker, True, redacted


def redact_command(command: list[str]) -> list[str]:
    return [SECRET_CONTENT.sub(REDACTED, part) for part in command]
