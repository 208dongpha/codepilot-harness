from pathlib import Path

import pytest
from pydantic import ValidationError

from codepilot.config import SandboxConfig, VerificationConfig
from codepilot.execution.docker_runtime import DockerError, DockerRuntime, SandboxCommandResult
from codepilot.verification.test_runner import DockerTestRunner


class Captured:
    def __init__(self) -> None:
        self.root: Path | None = None
        self.command: list[str] | None = None
        self.timeout_seconds: int | None = None
        self.max_output_chars: int | None = None


def test_default_and_explicit_verification_config():
    assert VerificationConfig().command == "python -m pytest -q"
    explicit = VerificationConfig(
        command="python -m unittest discover -q",
        timeout_seconds=30,
        max_output_chars=1000,
    )
    assert explicit.timeout_seconds == 30
    assert explicit.max_output_chars == 1000


@pytest.mark.parametrize(
    "command",
    ["", "   ", "python -c 'unterminated", "python\n-m pytest", "python\x00-m pytest"],
)
def test_invalid_verification_commands_are_rejected(command):
    with pytest.raises(ValidationError):
        VerificationConfig(command=command)


def test_runner_passes_workspace_and_bounded_limits(tmp_path, monkeypatch):
    captured = Captured()

    def fake_run_workspace(self, root, command, *, timeout_seconds, max_output_chars):
        captured.root = root
        captured.command = command
        captured.timeout_seconds = timeout_seconds
        captured.max_output_chars = max_output_chars
        return SandboxCommandResult("3 passed\n", "", 0, False, False)

    monkeypatch.setattr(DockerRuntime, "run_workspace", fake_run_workspace)
    runner = DockerTestRunner(
        VerificationConfig(
            command="python -m pytest -q",
            timeout_seconds=45,
            max_output_chars=2000,
        ),
        SandboxConfig(),
    )

    result = runner.run(tmp_path)

    assert captured.root == tmp_path
    assert captured.command == ["python", "-m", "pytest", "-q"]
    assert captured.timeout_seconds == 45
    assert captured.max_output_chars == 2000
    assert result.exit_code == 0
    assert result.timed_out is False
    assert result.stdout == "3 passed\n"
    assert result.error is None


def test_runner_maps_docker_unavailable_to_safe_error(tmp_path, monkeypatch):
    def unavailable(self, root, command, *, timeout_seconds, max_output_chars):
        raise DockerError("Docker daemon unavailable. No host fallback.")

    monkeypatch.setattr(DockerRuntime, "run_workspace", unavailable)
    runner = DockerTestRunner(VerificationConfig(), SandboxConfig())

    result = runner.run(tmp_path)

    assert result.exit_code is None
    assert result.error == "SANDBOX_UNAVAILABLE"
    assert result.stdout == ""
    assert result.stderr == ""


def test_runner_maps_missing_image_and_timeout(tmp_path, monkeypatch):
    messages = {
        "Build image: docker build -f docker/Dockerfile.sandbox": "SANDBOX_IMAGE_MISSING",
        "Docker command timed out.": "DOCKER_TIMEOUT",
    }
    for message, code in messages.items():
        def failing(self, root, command, *, timeout_seconds, max_output_chars, value=message):
            raise DockerError(value)

        monkeypatch.setattr(DockerRuntime, "run_workspace", failing)
        result = DockerTestRunner(VerificationConfig(), SandboxConfig()).run(tmp_path)
        assert result.error == code


def test_runner_redacts_and_bounds_output(tmp_path, monkeypatch):
    def fake_run_workspace(self, root, command, *, timeout_seconds, max_output_chars):
        return SandboxCommandResult(
            'api_key = "synthetic-private-value"\n' + "x" * 1000,
            "total failure\n",
            1,
            False,
            True,
        )

    monkeypatch.setattr(DockerRuntime, "run_workspace", fake_run_workspace)
    runner = DockerTestRunner(
        VerificationConfig(max_output_chars=256), SandboxConfig()
    )

    result = runner.run(tmp_path)

    assert result.exit_code == 1
    assert result.output_truncated
    assert result.output_redacted
    assert "synthetic-private-value" not in result.stdout
    assert "REDACTED" in result.stdout
    assert len(result.stdout) <= 256
    assert result.stderr == "total failure\n"


def test_runner_redacts_secret_like_command_arguments(tmp_path, monkeypatch):
    def fake_run_workspace(self, root, command, *, timeout_seconds, max_output_chars):
        return SandboxCommandResult("", "", 0, False, False)

    monkeypatch.setattr(DockerRuntime, "run_workspace", fake_run_workspace)
    runner = DockerTestRunner(
        VerificationConfig(command="python -c \"api_key = 'synthetic-private-value'\""),
        SandboxConfig(),
    )

    result = runner.run(tmp_path)

    assert all("synthetic-private-value" not in part for part in result.command)
    assert any("REDACTED" in part for part in result.command)
