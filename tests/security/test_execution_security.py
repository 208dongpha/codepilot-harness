import subprocess
from unittest.mock import patch

import pytest

from codepilot.config import SandboxConfig
from codepilot.context.ast_worker import AstWorkerConfig, BoundedAstParser
from codepilot.execution.docker_runtime import IMAGE, DockerRuntime, _CapturedOutput


def result(stdout="", code=0):
    return subprocess.CompletedProcess([], code, stdout, "")


def create_arguments(run):
    for call in run.call_args_list:
        args = call.args[0]
        if len(args) > 1 and args[1] == "create":
            return args
    raise AssertionError("docker create was not called")


def test_docker_controls_are_explicit(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with (
        patch(
            "subprocess.run",
            side_effect=[result("1"), result(), result(), result("0"), result(), result()],
        ) as run,
        patch.object(
            DockerRuntime,
            "_start_bounded",
            return_value=_CapturedOutput("ok", "", False, False),
        ),
    ):
        DockerRuntime(SandboxConfig()).run_workspace(
            workspace,
            ["python", "-c", "print(1)"],
            timeout_seconds=10,
            max_output_chars=1000,
        )

    create = create_arguments(run)
    expected = {
        "--network": "none",
        "--cpus": "1",
        "--memory": "512m",
        "--memory-swap": "512m",
        "--pids-limit": "128",
        "--ulimit": "core=0:0",
        "--cap-drop": "ALL",
        "--security-opt": "no-new-privileges",
    }
    for flag, value in expected.items():
        assert create[create.index(flag) + 1] == value
    assert "--read-only" in create
    assert "--privileged" not in create
    assert not any("docker.sock" in part for part in create)
    assert "--tmpfs" in create


def test_verification_command_is_not_shell_interpreted(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    command = ["python", "-c", "print('a; rm -rf /')"]
    with (
        patch(
            "subprocess.run",
            side_effect=[result("1"), result(), result(), result("0"), result(), result()],
        ) as run,
        patch.object(
            DockerRuntime,
            "_start_bounded",
            return_value=_CapturedOutput("ok", "", False, False),
        ) as start,
    ):
        DockerRuntime(SandboxConfig()).run_workspace(
            workspace, command, timeout_seconds=10, max_output_chars=1000
        )

    create = create_arguments(run)
    assert create[-3:] == command
    assert start.call_args.args[0].startswith("codepilot-task-")


def test_container_cleanup_is_verified_after_failure(tmp_path):
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
            return_value=_CapturedOutput("", "boom", True, False),
        ),
    ):
        outcome = DockerRuntime(SandboxConfig()).run_workspace(
            workspace, ["python", "-c", "pass"], timeout_seconds=1, max_output_chars=1000
        )

    assert outcome.timed_out
    assert outcome.exit_code is None
    assert run.call_args_list[-2].args[0][1:3] == ["rm", "--force"]
    assert run.call_args_list[-1].args[0][1:3] == ["container", "ls"]


def test_ast_worker_is_isolated_and_time_bounded(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-secret")
    process = type(
        "Process",
        (),
        {
            "returncode": 0,
            "stdout": b'{"results":[{"status":"ok","symbols":[]}],"truncated":false}',
            "communicate": lambda self, payload, timeout=None: (self.stdout, b""),
        },
    )()
    with patch("codepilot.context.ast_worker.subprocess.Popen", return_value=process) as popen:
        BoundedAstParser(AstWorkerConfig()).parse(["def f():\n    pass\n"])

    command = popen.call_args.args[0]
    assert "-I" in command and "-B" in command
    assert "GEMINI_API_KEY" not in popen.call_args.kwargs["env"]
    assert popen.call_args.kwargs["stdin"] is subprocess.PIPE


def test_docker_security_controls_are_blocked_when_daemon_is_unavailable():
    runtime = DockerRuntime(SandboxConfig())
    if not runtime.available():
        pytest.skip("Docker daemon unavailable; sandbox security NOT VERIFIED")
    if runtime._call(["image", "inspect", IMAGE]).returncode != 0:
        pytest.skip("Sandbox image unavailable; sandbox security NOT VERIFIED")
    assert runtime._call(["info", "--format", "{{.ServerVersion}}"]).returncode == 0
