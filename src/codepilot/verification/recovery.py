import json

from codepilot.llm.base import Message
from codepilot.verification.models import VerificationResult
from codepilot.verification.redaction import bound_and_redact

_MAX_TEST_EVIDENCE = 3000
_MAX_CHANGES = 50


def verification_feedback(
    result: VerificationResult, attempt: int, max_attempts: int
) -> Message:
    test = result.test
    evidence = ""
    if test is not None:
        evidence, _, _ = bound_and_redact(
            "\n".join(part for part in (test.stdout, test.stderr) if part),
            _MAX_TEST_EVIDENCE,
        )
    payload = {
        "verification": result.verdict.value,
        "reasons": result.reasons[:8],
        "repair_attempt": attempt,
        "max_repair_attempts": max_attempts,
        "tests": {
            "command": test.command if test is not None else [],
            "exit_code": test.exit_code if test is not None else None,
            "timed_out": test.timed_out if test is not None else False,
        },
        "untrusted_test_output": evidence,
        "changed_files": [
            {"path": change.path, "change": change.change, "authorized": change.authorized}
            for change in result.diff.changes[:_MAX_CHANGES]
        ],
        "unauthorized_changes": result.diff.unauthorized[:_MAX_CHANGES],
        "instruction": (
            "The Harness ran the configured tests in Docker and did not accept the task. "
            "Use only declared tools to repair the implementation. Do not claim success "
            "without a new Harness verification. Do not modify files outside approved "
            "patch targets."
        ),
    }
    return Message(role="user", content=json.dumps(payload, separators=(",", ":")))
