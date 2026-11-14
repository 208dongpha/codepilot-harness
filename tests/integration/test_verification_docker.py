import hashlib
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from codepilot.agent.loop import AgentLoop
from codepilot.agent.state import AgentStatus
from codepilot.config import RepositoryConfig, SandboxConfig, VerificationConfig
from codepilot.context.builder import ContextBuilder
from codepilot.context.repository_map import RepositoryMapper
from codepilot.context.token_budget import TokenBudget
from codepilot.execution.docker_runtime import IMAGE, DockerRuntime
from codepilot.llm.base import ModelTurn, ToolCall
from codepilot.patch.tool import IsolatedWorkspace, PatchTool
from codepilot.permissions.engine import PermissionEngine
from codepilot.tools.factory import create_read_only_registry
from codepilot.tools.workspace import Workspace
from codepilot.verification.diff import GitDiffVerifier
from codepilot.verification.engine import VerificationEngine
from codepilot.verification.models import VerificationVerdict
from codepilot.verification.test_runner import DockerTestRunner


@pytest.fixture(scope="module")
def docker_runtime():
    runtime = DockerRuntime(SandboxConfig())
    if not runtime.available():
        pytest.skip("Docker daemon unavailable; Docker verification NOT VERIFIED")
    if runtime._call(["image", "inspect", IMAGE]).returncode != 0:
        pytest.skip("Sandbox image is not built; Docker verification NOT VERIFIED")
    return runtime


def test_real_test_runner_runs_in_docker_with_security_limits(
    tmp_path, docker_runtime, monkeypatch
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "test_ok.py").write_text(
        "import unittest\n\n"
        "class TestOk(unittest.TestCase):\n"
        "    def test_ok(self):\n"
        "        self.assertEqual(1, 1)\n",
        encoding="utf-8",
    )
    original_call = docker_runtime._call
    observed = []

    def inspecting_call(self, args, timeout=10):
        response = original_call(args, timeout)
        if args[0] == "create" and response.returncode == 0:
            name = args[args.index("--name") + 1]
            template = (
                "{{.Config.User}}|{{.HostConfig.NetworkMode}}|"
                "{{.HostConfig.ReadonlyRootfs}}|{{.HostConfig.Privileged}}|"
                "{{len .Mounts}}|{{json .HostConfig.CapDrop}}|"
                "{{json .HostConfig.SecurityOpt}}|{{json .HostConfig.Binds}}|"
                "{{json .HostConfig.Tmpfs}}|{{.HostConfig.NanoCpus}}|"
                "{{.HostConfig.Memory}}|{{.HostConfig.PidsLimit}}|"
                "{{.HostConfig.MemorySwap}}|{{json .HostConfig.Ulimits}}|"
                "{{json .Config.Env}}"
            )
            inspection = original_call(["inspect", "--format", template, name])
            observed.append((inspection.returncode, inspection.stdout.strip()))
        return response

    monkeypatch.setattr(DockerRuntime, "_call", inspecting_call)
    runner = DockerTestRunner(
        VerificationConfig(
            command="python -m unittest discover -q -s . -p test_*.py",
            timeout_seconds=30,
            max_output_chars=4000,
        ),
        SandboxConfig(),
    )

    result = runner.run(workspace)

    assert result.error is None
    assert result.exit_code == 0
    assert result.timed_out is False
    assert len(observed) == 1
    code, settings = observed[0]
    assert code == 0
    values = settings.split("|")
    assert values[0] != "0:0"
    assert values[1] == "none"
    assert values[2] == "true"
    assert values[3] == "false"
    assert values[4] == "1"
    assert '"ALL"' in values[5]
    assert "no-new-privileges" in values[6]
    binds = json.loads(values[7])
    tmpfs = json.loads(values[8])
    environment = json.loads(values[14])
    assert values[9] == "1000000000"
    assert values[10] == "536870912"
    assert values[11] == "128"
    assert values[12] == "536870912"
    assert json.loads(values[13]) == [{"Name": "core", "Soft": 0, "Hard": 0}]
    assert f"{workspace.absolute()}:/workspace:rw" in binds
    assert "/tmp" in tmpfs
    assert "GEMINI_API_KEY" not in environment
    assert "HOME=/tmp" in environment


def test_real_test_runner_returns_nonzero_exit(tmp_path, docker_runtime):
    runner = DockerTestRunner(
        VerificationConfig(
            command="python -c \"import sys; sys.exit(7)\"",
            timeout_seconds=30,
            max_output_chars=4000,
        ),
        SandboxConfig(),
    )

    result = runner.run(tmp_path)

    assert result.error is None
    assert result.exit_code == 7
    assert result.timed_out is False


def test_real_test_runner_timeout_is_bounded_and_cleaned_up(tmp_path, docker_runtime):
    runner = DockerTestRunner(
        VerificationConfig(
            command="python -c \"import time; time.sleep(30)\"",
            timeout_seconds=1,
            max_output_chars=4000,
        ),
        SandboxConfig(),
    )
    started = time.monotonic()

    result = runner.run(tmp_path)

    assert time.monotonic() - started < 60
    assert result.timed_out is True
    assert result.exit_code is None
    assert result.error is None


def test_real_test_runner_supports_default_pytest_command(tmp_path, docker_runtime):
    probe = docker_runtime._call(
        ["run", "--rm", "--network", "none", IMAGE, "python", "-m", "pytest", "--version"],
        timeout=30,
    )
    if probe.returncode != 0:
        pytest.skip("Sandbox image lacks pytest; rebuild docker/Dockerfile.sandbox")
    (tmp_path / "test_ok.py").write_text(
        "def test_ok():\n    assert 1 + 1 == 2\n", encoding="utf-8"
    )

    result = DockerTestRunner(VerificationConfig(), SandboxConfig()).run(tmp_path)

    assert result.error is None
    assert result.exit_code == 0
    assert "1 passed" in result.stdout


def test_real_verification_engine_passes_authorized_change(tmp_path, docker_runtime):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "app.py").write_text("value = 1\n", encoding="utf-8")
    (workspace_root / "test_app.py").write_text(
        "import unittest\n\n"
        "class TestApp(unittest.TestCase):\n"
        "    def test_value(self):\n"
        "        from app import value\n"
        "        self.assertEqual(value, 2)\n",
        encoding="utf-8",
    )
    config = RepositoryConfig(workspace=workspace_root, max_total_bytes=100000)
    workspace = Workspace(config)
    engine = VerificationEngine(
        workspace=workspace,
        runner=DockerTestRunner(
            VerificationConfig(
                command="python -m unittest discover -q -s . -p test_*.py",
                timeout_seconds=30,
                max_output_chars=4000,
            ),
            SandboxConfig(),
        ),
        differ=GitDiffVerifier(workspace, config.max_total_bytes),
        max_total_bytes=config.max_total_bytes,
    )
    engine.prepare()
    (workspace_root / "app.py").write_text("value = 2\n", encoding="utf-8")
    approved = hashlib.sha256(b"value = 2\n").hexdigest()

    result = engine.verify({"app.py": approved})

    assert result.verdict is VerificationVerdict.PASS
    assert result.test.exit_code == 0
    assert result.diff.unauthorized == []


async def test_real_recovery_workflow_fails_then_repairs_and_passes(tmp_path, docker_runtime):
    host = tmp_path / "host"
    host.mkdir()
    (host / "app.py").write_text("value = 1\n", encoding="utf-8")
    (host / "test_app.py").write_text(
        "import unittest\n\n"
        "class TestApp(unittest.TestCase):\n"
        "    def test_value(self):\n"
        "        from app import value\n"
        "        self.assertEqual(value, 2)\n",
        encoding="utf-8",
    )
    config = RepositoryConfig(
        workspace=host, max_total_bytes=100000, max_context_chars=4000
    )
    workspace = Workspace(config)
    isolated = IsolatedWorkspace.create(workspace)
    isolated_config = config.model_copy(update={"workspace": isolated.root})
    isolated_workspace = Workspace(isolated_config)
    permissions = PermissionEngine()
    provider = SimpleNamespace(
        generate_turn=AsyncMock(),
        count_tokens=AsyncMock(return_value=100),
    )
    verifier = VerificationEngine(
        workspace=isolated_workspace,
        runner=DockerTestRunner(
            VerificationConfig(
                command="python -m unittest discover -q -s . -p test_*.py",
                timeout_seconds=30,
                max_output_chars=4000,
            ),
            SandboxConfig(),
        ),
        differ=GitDiffVerifier(isolated_workspace, config.max_total_bytes),
        max_total_bytes=config.max_total_bytes,
    )
    loop = AgentLoop(
        provider=provider,
        registry=create_read_only_registry(isolated_config),
        mapper=RepositoryMapper(workspace),
        context_builder=ContextBuilder(workspace),
        permissions=permissions,
        patch_tool=PatchTool(isolated, permissions),
        token_budget=TokenBudget(8000, 1000, provider.count_tokens),
        max_steps=6,
        verifier=verifier,
        max_verification_retries=3,
    )
    provider.generate_turn.side_effect = [
        ModelTurn(
            tool_calls=[
                ToolCall(
                    id="patch-1",
                    name="apply_patch",
                    arguments={
                        "operation": "replace",
                        "target": "app.py",
                        "content": "value = 1\n",
                    },
                )
            ]
        ),
        ModelTurn(text="First attempt is complete."),
        ModelTurn(
            tool_calls=[
                ToolCall(
                    id="patch-2",
                    name="apply_patch",
                    arguments={
                        "operation": "replace",
                        "target": "app.py",
                        "content": "value = 2\n",
                    },
                )
            ]
        ),
        ModelTurn(text="Repaired."),
    ]
    try:
        waiting = await loop.start("Change app.py so the test passes")
        assert waiting.state.status is AgentStatus.WAITING_APPROVAL
        permissions.approve(waiting.pending.action.action_id)
        waiting = await loop.resume()
        assert waiting.state.status is AgentStatus.WAITING_APPROVAL
        assert waiting.verification.verdict is VerificationVerdict.FAIL
        feedback = "\n".join(
            message.get("content", "")
            for message in provider.generate_turn.call_args_list[-1].args[0]
        )
        assert "TESTS_FAILED" in feedback

        permissions.approve(waiting.pending.action.action_id)
        result = await loop.resume()

        assert result.state.status is AgentStatus.COMPLETED
        assert result.report.verdict is VerificationVerdict.PASS
        assert result.report.retry_count == 1
        assert result.report.verification_attempts == 2
        assert [item.verdict for item in result.verifications] == [
            VerificationVerdict.FAIL,
            VerificationVerdict.PASS,
        ]
        assert (isolated.root / "app.py").read_text(encoding="utf-8") == "value = 2\n"
        assert (host / "app.py").read_text(encoding="utf-8") == "value = 1\n"
    finally:
        isolated.close()
