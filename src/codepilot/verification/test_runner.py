import logging
import shlex
import time
from pathlib import Path

from codepilot.config import ConfigurationError, SandboxConfig, VerificationConfig
from codepilot.execution.docker_runtime import DockerError, DockerRuntime
from codepilot.verification.models import TestExecution
from codepilot.verification.redaction import bound_and_redact, redact_command

logger = logging.getLogger("codepilot")


def _error_code(exc: DockerError) -> str:
    message = str(exc)
    if "daemon unavailable" in message:
        return "SANDBOX_UNAVAILABLE"
    if "Build image" in message:
        return "SANDBOX_IMAGE_MISSING"
    if "cleanup failed" in message:
        return "SANDBOX_CLEANUP_FAILED"
    if "timed out" in message:
        return "DOCKER_TIMEOUT"
    return "SANDBOX_ERROR"


class DockerTestRunner:
    """Run the configured test command inside the disposable Docker sandbox."""

    def __init__(self, config: VerificationConfig, sandbox: SandboxConfig) -> None:
        self.config = config
        self.sandbox = sandbox
        try:
            command = shlex.split(config.command, posix=True)
        except ValueError:
            raise ConfigurationError("Invalid verification command.") from None
        if not command:
            raise ConfigurationError("Invalid verification command.")
        self.command = command

    def run(self, workspace_root: Path) -> TestExecution:
        started = time.monotonic()
        try:
            result = DockerRuntime(self.sandbox).run_workspace(
                workspace_root,
                self.command,
                timeout_seconds=self.config.timeout_seconds,
                max_output_chars=self.config.max_output_chars,
            )
        except DockerError as exc:
            logger.error("test_runner FAIL")
            return TestExecution(
                command=redact_command(self.command),
                duration_seconds=time.monotonic() - started,
                error=_error_code(exc),
            )

        stdout, stdout_truncated, stdout_redacted = bound_and_redact(
            result.stdout, self.config.max_output_chars
        )
        stderr, stderr_truncated, stderr_redacted = bound_and_redact(
            result.stderr, self.config.max_output_chars
        )
        if result.timed_out:
            outcome = "TIMEOUT"
        elif result.exit_code == 0:
            outcome = "PASS"
        else:
            outcome = "FAIL"
        logger.info(f"test_runner {outcome}")
        return TestExecution(
            command=redact_command(self.command),
            exit_code=result.exit_code,
            timed_out=result.timed_out,
            duration_seconds=time.monotonic() - started,
            stdout=stdout,
            stderr=stderr,
            output_truncated=result.truncated or stdout_truncated or stderr_truncated,
            output_redacted=stdout_redacted or stderr_redacted,
        )
