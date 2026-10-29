import subprocess
from unittest.mock import patch

import pytest

from codepilot.config import SandboxConfig
from codepilot.execution.docker_runtime import DockerError, DockerRuntime


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
    assert run.call_args_list[-1].args[0][1:3] == ["rm", "--force"]


def test_timeout_cleanup():
    with patch(
        "subprocess.run",
        side_effect=[
            result("1"),
            result(),
            result(),
            subprocess.TimeoutExpired("docker", 1),
            result(),
        ],
    ) as run:
        with pytest.raises(DockerError, match="timed out"):
            DockerRuntime(SandboxConfig()).smoke_test()
        assert run.call_args_list[-1].args[0][1] == "rm"


def test_nonzero_exit_cleanup():
    with patch(
        "subprocess.run",
        side_effect=[result("1"), result(), result(), result("bad"), result("1"), result()],
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
        ],
    ):
        with pytest.raises(DockerError, match="cleanup failed"):
            DockerRuntime(SandboxConfig()).smoke_test()
