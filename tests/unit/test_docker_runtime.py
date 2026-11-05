import subprocess
from unittest.mock import patch

import pytest

from codepilot.config import SandboxConfig
from codepilot.execution.docker_runtime import (
    DockerError,
    DockerRuntime,
    _BoundedBuffer,
    _CapturedOutput,
)


def result(stdout="", code=0):
    return subprocess.CompletedProcess([], code, stdout, "")


def test_docker_unavailable():
    with patch("subprocess.run", side_effect=FileNotFoundError) as run:
        runtime = DockerRuntime(SandboxConfig())
        assert not runtime.available()
        with pytest.raises(DockerError, match="No host fallback"):
            runtime.smoke_test()
        assert all(call.args[0][0] == "docker" for call in run.call_args_list)


def test_success_security_and_cleanup():
    with patch(
        "subprocess.run",
        side_effect=[
            result("1"),
            result(),
            result("id"),
            result("codepilot-smoke-ok\n"),
            result("0"),
            result(),
            result(),
        ],
    ) as run:
        assert DockerRuntime(SandboxConfig()).smoke_test().exit_code == 0
    create = run.call_args_list[2].args[0]
    for flag, value in [
        ("--network", "none"),
        ("--user", "10001:10001"),
        ("--cpus", "1"),
        ("--memory", "256m"),
        ("--pids-limit", "64"),
    ]:
        assert create[create.index(flag) + 1] == value
    assert create[create.index("--cap-drop") + 1] == "ALL"
    assert create[create.index("--security-opt") + 1] == "no-new-privileges"
    assert "--read-only" in create
    assert "--env" not in create
    assert "--mount" not in create
    assert "--privileged" not in create
    assert "--volume" not in create
    name = create[create.index("--name") + 1]
    assert run.call_args_list[-2].args[0] == ["docker", "rm", "--force", name]
    verification = run.call_args_list[-1].args[0]
    assert verification[1:3] == ["container", "ls"]
    assert verification[verification.index("--filter") + 1] == f"name=^/{name}$"


def test_timeout_cleanup():
    with patch(
        "subprocess.run",
        side_effect=[
            result("1"),
            result(),
            result(),
            subprocess.TimeoutExpired("docker", 1),
            result(),
            result(),
        ],
    ) as run:
        with pytest.raises(DockerError, match="timed out"):
            DockerRuntime(SandboxConfig()).smoke_test()
        assert run.call_args_list[-2].args[0][1] == "rm"
        assert run.call_args_list[-1].args[0][1:3] == ["container", "ls"]


def test_nonzero_exit_cleanup():
    with patch(
        "subprocess.run",
        side_effect=[
            result("1"),
            result(),
            result(),
            result("bad"),
            result("1"),
            result(),
            result(),
        ],
    ):
        with pytest.raises(DockerError, match="failed"):
            DockerRuntime(SandboxConfig()).smoke_test()


def test_cleanup_failure_is_reported():
    with patch(
        "subprocess.run",
        side_effect=[
            result("1"),
            result(),
            result(),
            result("codepilot-smoke-ok"),
            result("0"),
            result(code=1),
            result(),
        ],
    ):
        with pytest.raises(DockerError, match="cleanup failed"):
            DockerRuntime(SandboxConfig()).smoke_test()


@pytest.mark.parametrize(
    "cleanup_result",
    [
        subprocess.TimeoutExpired("docker", 10),
        result(code=1),
        FileNotFoundError(),
    ],
)
@pytest.mark.parametrize(
    "verification_result,status",
    [
        (result(), "absence verified"),
        (result("container|running"), "still exists"),
        (result(code=1), "NOT VERIFIED"),
        (subprocess.TimeoutExpired("docker", 10), "NOT VERIFIED"),
    ],
)
def test_run_timeout_and_cleanup_error_preserve_both_failures(
    cleanup_result,
    verification_result,
    status,
):
    with patch(
        "subprocess.run",
        side_effect=[
            result("1"),
            result(),
            result(),
            subprocess.TimeoutExpired("docker", 1),
            cleanup_result,
            verification_result,
        ],
    ) as run:
        with pytest.raises(DockerError) as caught:
            DockerRuntime(SandboxConfig()).smoke_test()
        message = str(caught.value)
        assert "timed out" in message
        assert "cleanup failed" in message
        assert status in message
        calls = [call.args[0] for call in run.call_args_list]
        name = calls[2][calls[2].index("--name") + 1]
        assert calls[4] == ["docker", "rm", "--force", name]
        assert calls[5][1:3] == ["container", "ls"]
        assert calls[5][calls[5].index("--filter") + 1] == f"name=^/{name}$"
        assert all(call.kwargs["timeout"] > 0 for call in run.call_args_list)
        assert all(command[0] == "docker" for command in calls)


@pytest.mark.parametrize(
    "verification_result,status",
    [
        (result("container|exited"), "still exists"),
        (result(code=1), "NOT VERIFIED"),
        (subprocess.TimeoutExpired("docker", 10), "NOT VERIFIED"),
    ],
)
def test_successful_rm_requires_final_state_evidence(verification_result, status):
    with (
        patch(
            "subprocess.run",
            side_effect=[
                result("1"),
                result(),
                result(),
                result("codepilot-smoke-ok"),
                result("0"),
                result(),
                verification_result,
            ],
        ),
        patch("codepilot.execution.docker_runtime.logger") as logger,
    ):
        with pytest.raises(DockerError, match=status):
            DockerRuntime(SandboxConfig()).smoke_test()
        assert not any(call.args == ("docker_smoke PASS",) for call in logger.info.call_args_list)


def test_create_timeout_still_requests_removal_and_verifies():
    with patch(
        "subprocess.run",
        side_effect=[
            result("1"),
            result(),
            subprocess.TimeoutExpired("docker", 10),
            result(),
            result(),
        ],
    ) as run:
        with pytest.raises(DockerError, match="timed out"):
            DockerRuntime(SandboxConfig()).smoke_test()
        assert run.call_args_list[-2].args[0][1:3] == ["rm", "--force"]
        assert run.call_args_list[-1].args[0][1:3] == ["container", "ls"]


def test_bounded_buffer_discards_excess_output():
    buffer = _BoundedBuffer(4)
    buffer.append(b"abcdef")
    assert buffer.text() == "abcd"
    assert buffer.truncated


def test_run_workspace_security_limits_and_cleanup(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with (
        patch(
            "subprocess.run",
            side_effect=[
                result("1"),
                result(),
                result(),
                result("0"),
                result(),
                result(),
            ],
        ) as run,
        patch.object(
            DockerRuntime,
            "_start_bounded",
            return_value=_CapturedOutput("2 passed\n", "", False, False),
        ),
    ):
        outcome = DockerRuntime(SandboxConfig()).run_workspace(
            workspace,
            ["python", "-m", "pytest"],
            timeout_seconds=30,
            max_output_chars=2000,
        )

    assert outcome.exit_code == 0
    assert outcome.stdout == "2 passed\n"
    assert outcome.timed_out is False
    create = run.call_args_list[2].args[0]
    for flag, value in [
        ("--network", "none"),
        ("--cpus", "1"),
        ("--memory", "512m"),
        ("--pids-limit", "128"),
        ("--workdir", "/workspace"),
        ("--volume", f"{workspace.absolute()}:/workspace:rw"),
        ("--tmpfs", "/tmp:rw,nosuid,nodev,size=64m"),
    ]:
        assert create[create.index(flag) + 1] == value
    user = create[create.index("--user") + 1]
    assert user != "0:0"
    assert ":" in user
    assert create[create.index("--cap-drop") + 1] == "ALL"
    assert create[create.index("--security-opt") + 1] == "no-new-privileges"
    assert "--read-only" in create
    assert "--privileged" not in create
    assert not any("docker.sock" in part for part in create)
    env_values = [create[index + 1] for index, part in enumerate(create) if part == "--env"]
    assert env_values == [
        "HOME=/tmp",
        "TMPDIR=/tmp",
        "PYTHONDONTWRITEBYTECODE=1",
        "PYTHONUNBUFFERED=1",
    ]
    assert create[-3:] == ["python", "-m", "pytest"]
    name = create[create.index("--name") + 1]
    assert run.call_args_list[-2].args[0] == ["docker", "rm", "--force", name]
    assert run.call_args_list[-1].args[0][1:3] == ["container", "ls"]


def test_run_workspace_timeout_returns_no_exit_and_cleans_up(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with (
        patch(
            "subprocess.run",
            side_effect=[result("1"), result(), result(), result(), result()],
        ) as run,
        patch.object(
            DockerRuntime,
            "_start_bounded",
            return_value=_CapturedOutput("partial", "timeout", True, False),
        ),
    ):
        outcome = DockerRuntime(SandboxConfig()).run_workspace(
            workspace,
            ["python", "-c", "import time; time.sleep(30)"],
            timeout_seconds=1,
            max_output_chars=2000,
        )

    assert outcome.timed_out is True
    assert outcome.exit_code is None
    assert outcome.stdout == "partial"
    assert run.call_args_list[-2].args[0][1:3] == ["rm", "--force"]
    assert run.call_args_list[-1].args[0][1:3] == ["container", "ls"]


def test_run_workspace_requires_sandbox_image(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with patch("subprocess.run", side_effect=[result("1"), result(code=1)]) as run:
        with pytest.raises(DockerError, match="Build image"):
            DockerRuntime(SandboxConfig()).run_workspace(
                workspace,
                ["python", "-c", "print(1)"],
                timeout_seconds=10,
                max_output_chars=1000,
            )
    assert all(call.args[0][0] == "docker" for call in run.call_args_list)


def test_run_workspace_never_falls_back_to_host(tmp_path):
    with (
        patch("subprocess.run", side_effect=FileNotFoundError) as run,
        patch("subprocess.Popen") as popen,
    ):
        with pytest.raises(DockerError, match="No host fallback"):
            DockerRuntime(SandboxConfig()).run_workspace(
                tmp_path,
                ["python", "-c", "print(1)"],
                timeout_seconds=10,
                max_output_chars=1000,
            )
    assert run.call_count == 1
    assert run.call_args.args[0][0] == "docker"
    popen.assert_not_called()


@pytest.mark.parametrize("command", [[], [""], ["python", "\x00"]])
def test_run_workspace_rejects_invalid_commands(tmp_path, command):
    with patch("subprocess.run") as run:
        with pytest.raises(DockerError, match="Invalid sandbox command"):
            DockerRuntime(SandboxConfig()).run_workspace(
                tmp_path,
                command,
                timeout_seconds=10,
                max_output_chars=1000,
            )
    run.assert_not_called()


def test_run_workspace_rejects_missing_workspace(tmp_path):
    with patch("subprocess.run") as run:
        with pytest.raises(DockerError, match="Invalid sandbox workspace"):
            DockerRuntime(SandboxConfig()).run_workspace(
                tmp_path / "missing",
                ["python", "-c", "print(1)"],
                timeout_seconds=10,
                max_output_chars=1000,
            )
    run.assert_not_called()
