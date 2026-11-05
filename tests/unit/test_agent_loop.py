import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from codepilot.agent.loop import AgentLoop
from codepilot.agent.state import AgentStatus
from codepilot.config import RepositoryConfig
from codepilot.context.builder import ContextBuilder
from codepilot.context.repository_map import RepositoryMapper
from codepilot.context.token_budget import TokenBudget
from codepilot.llm.base import ModelTurn, ProviderError, ToolCall
from codepilot.patch.tool import IsolatedWorkspace, PatchTool
from codepilot.permissions.engine import PermissionEngine
from codepilot.tools.factory import create_read_only_registry
from codepilot.tools.workspace import Workspace
from codepilot.verification.models import (
    DiffReport,
    FileChange,
    TestExecution,
    VerificationResult,
    VerificationVerdict,
)


@pytest.fixture
def harness(tmp_path):
    host = tmp_path / "host"
    host.mkdir()
    (host / "app.py").write_text("value = 1\n", encoding="utf-8")
    config = RepositoryConfig(workspace=host, max_context_chars=4000)
    workspace = Workspace(config)
    isolated = IsolatedWorkspace.create(workspace)
    permissions = PermissionEngine()
    provider = SimpleNamespace(
        generate_turn=AsyncMock(),
        count_tokens=AsyncMock(return_value=100),
    )
    loop = AgentLoop(
        provider=provider,
        registry=create_read_only_registry(config.model_copy(update={"workspace": isolated.root})),
        mapper=RepositoryMapper(workspace),
        context_builder=ContextBuilder(workspace),
        permissions=permissions,
        patch_tool=PatchTool(isolated, permissions),
        token_budget=TokenBudget(8000, 1000, provider.count_tokens),
        max_steps=4,
    )
    try:
        yield host, isolated, permissions, provider, loop
    finally:
        isolated.close()


async def test_loop_executes_registered_read_tool_and_returns_final_text(harness):
    _, _, _, provider, loop = harness
    provider.generate_turn.side_effect = [
        ModelTurn(tool_calls=[ToolCall(id="1", name="read_file", arguments={"path": "app.py"})]),
        ModelTurn(text="Found the value."),
    ]

    result = await loop.start("Read app.py")

    assert result.state.status is AgentStatus.COMPLETED
    assert result.response == "Found the value."
    sent = provider.generate_turn.call_args_list[1].args[0]
    assert any(
        message.get("role") == "tool" and "value = 1" in message["content"]
        for message in sent
    )


async def test_invalid_tool_call_is_bounded_and_returned_as_observation(harness):
    _, _, _, provider, loop = harness
    provider.generate_turn.side_effect = [
        ModelTurn(tool_calls=[ToolCall(id="1", name="unknown", arguments={})]),
        ModelTurn(text="Unable to use that tool."),
    ]
    result = await loop.start("Try an invalid tool")
    assert result.state.status is AgentStatus.COMPLETED
    sent = provider.generate_turn.call_args_list[1].args[0]
    assert any("UNKNOWN_TOOL" in message.get("content", "") for message in sent)


async def test_step_budget_prevents_infinite_tool_loop(harness):
    _, _, _, provider, loop = harness
    provider.generate_turn.return_value = ModelTurn(
        tool_calls=[ToolCall(id="1", name="list_files", arguments={})]
    )
    loop.max_steps = 2
    result = await loop.start("Keep listing")
    assert result.state.status is AgentStatus.FAILED
    assert result.state.error == "STEP_BUDGET_EXHAUSTED"
    assert provider.generate_turn.await_count == 2


async def test_incomplete_context_warning_is_sent_to_model(harness, monkeypatch):
    _, _, _, provider, loop = harness
    provider.generate_turn.return_value = ModelTurn(text="Done")
    original = loop.context_builder.build

    def incomplete(*args, **kwargs):
        context = original(*args, **kwargs)
        context.note("input_incomplete")
        return context

    monkeypatch.setattr(loop.context_builder, "build", incomplete)
    await loop.start("Find missing code")
    messages = provider.generate_turn.call_args.args[0]
    assert "must not be treated as evidence" in messages[0]["content"]


async def test_patch_waits_for_exact_approval_then_changes_only_isolated(harness):
    host, isolated, permissions, provider, loop = harness
    provider.generate_turn.side_effect = [
        ModelTurn(
            tool_calls=[
                ToolCall(
                    id="patch-1",
                    name="apply_patch",
                    arguments={
                        "operation": "replace",
                        "target": "app.py",
                        "content": "value = 2\n",
                    },
                )
            ]
        ),
        ModelTurn(text="Patch prepared."),
    ]
    waiting = await loop.start("Change the value")
    assert waiting.state.status is AgentStatus.WAITING_APPROVAL
    assert (host / "app.py").read_text(encoding="utf-8") == "value = 1\n"
    assert (isolated.root / "app.py").read_text(encoding="utf-8") == "value = 1\n"

    permissions.approve(waiting.pending.action.action_id)
    completed = await loop.resume()
    assert completed.state.status is AgentStatus.COMPLETED
    assert (isolated.root / "app.py").read_text(encoding="utf-8") == "value = 2\n"
    assert (host / "app.py").read_text(encoding="utf-8") == "value = 1\n"


async def test_calls_after_a_pending_patch_resume_in_order(harness):
    _, _, permissions, provider, loop = harness
    provider.generate_turn.side_effect = [
        ModelTurn(
            tool_calls=[
                ToolCall(
                    id="patch-1",
                    name="apply_patch",
                    arguments={
                        "operation": "replace",
                        "target": "app.py",
                        "content": "value = 2\n",
                    },
                ),
                ToolCall(id="read-1", name="read_file", arguments={"path": "app.py"}),
            ]
        ),
        ModelTurn(text="Done"),
    ]
    waiting = await loop.start("Change and read app")
    permissions.approve(waiting.pending.action.action_id)
    result = await loop.resume()
    assert result.state.status is AgentStatus.COMPLETED
    sent = provider.generate_turn.call_args_list[1].args[0]
    tool_messages = [message for message in sent if message.get("role") == "tool"]
    assert [message["call_id"] for message in tool_messages] == ["patch-1", "read-1"]
    assert "value = 2" in tool_messages[1]["content"]


async def test_patch_marks_original_repository_context_stale(harness):
    _, _, permissions, provider, loop = harness
    provider.generate_turn.side_effect = [
        ModelTurn(
            tool_calls=[
                ToolCall(
                    id="patch-1",
                    name="apply_patch",
                    arguments={
                        "operation": "replace",
                        "target": "app.py",
                        "content": "value = 2\n",
                    },
                )
            ]
        ),
        ModelTurn(text="Done"),
    ]
    waiting = await loop.start("Change app")
    permissions.approve(waiting.pending.action.action_id)
    await loop.resume()

    messages = provider.generate_turn.call_args_list[1].args[0]
    stale = [message for message in messages if "STALE_AFTER_PATCH" in message.get("content", "")]
    assert len(stale) == 1
    assert "app.py" in stale[0]["content"]


async def test_denial_and_cancellation_stop_pending_workflow(harness):
    _, isolated, permissions, provider, loop = harness
    provider.generate_turn.return_value = ModelTurn(
        tool_calls=[
            ToolCall(
                id="patch-1",
                name="apply_patch",
                arguments={"operation": "replace", "target": "app.py", "content": "changed"},
            )
        ]
    )
    waiting = await loop.start("Change app")
    permissions.deny(waiting.pending.action.action_id)
    loop.deny()
    assert loop.state.status is AgentStatus.CANCELLED
    assert (isolated.root / "app.py").read_text(encoding="utf-8") == "value = 1\n"


async def test_provider_failure_is_preserved_as_safe_error(harness):
    _, _, _, provider, loop = harness
    provider.generate_turn.side_effect = ProviderError("RATE_LIMIT_ERROR")
    result = await loop.start("Read code")
    assert result.state.status is AgentStatus.FAILED
    assert result.state.error == "RATE_LIMIT_ERROR"


class FakeVerifier:
    def __init__(self, results, fallback_diff=None):
        self.results = list(results)
        self.fallback_diff = fallback_diff or DiffReport()
        self.prepared = 0
        self.calls = []

    def prepare(self):
        self.prepared += 1

    def verify(self, authorized):
        self.calls.append(dict(authorized))
        return self.results.pop(0)

    def diff_report(self, authorized):
        return self.fallback_diff


def attach_verifier(loop, results, retries=3):
    verifier = FakeVerifier(results)
    loop.verifier = verifier
    loop.max_verification_retries = retries
    return verifier


def verification_result(
    verdict,
    *,
    reasons=None,
    test=None,
    changes=None,
    retryable=False,
    policy=None,
):
    return VerificationResult(
        verdict=verdict,
        reasons=reasons or [],
        test=test,
        diff=DiffReport(changes=changes or [], policy_violations=policy or []),
        retryable=retryable,
    )


async def test_verification_pass_completes_with_report(harness):
    _, _, _, provider, loop = harness
    verifier = attach_verifier(
        loop,
        [verification_result(VerificationVerdict.PASS, test=TestExecution(exit_code=0))],
    )
    provider.generate_turn.return_value = ModelTurn(text="Done")

    result = await loop.start("Implement feature")

    assert result.state.status is AgentStatus.COMPLETED
    assert verifier.prepared == 1
    assert verifier.calls == [{}]
    assert result.verification.verdict is VerificationVerdict.PASS
    assert result.report.verdict is VerificationVerdict.PASS
    assert result.report.verification_attempts == 1
    assert result.report.retry_count == 0


async def test_verification_setup_failure_fails_closed(harness):
    _, _, _, provider, loop = harness

    class FailingVerifier:
        def prepare(self):
            raise RuntimeError("baseline unavailable")

        def verify(self, authorized):
            raise AssertionError("must not verify")

    loop.verifier = FailingVerifier()

    result = await loop.start("Implement feature")

    assert result.state.status is AgentStatus.FAILED
    assert result.state.error == "VERIFICATION_SETUP_ERROR"
    assert provider.generate_turn.await_count == 0
    assert result.report.verdict is VerificationVerdict.INCONCLUSIVE
    assert "VERIFICATION_SETUP_ERROR" in result.report.remaining_issues


async def test_test_failure_sends_bounded_feedback_and_retries(harness):
    _, _, _, provider, loop = harness
    failure = TestExecution(
        command=["python", "-m", "pytest"],
        exit_code=1,
        stderr="assertion failed\n",
    )
    attach_verifier(
        loop,
        [
            verification_result(
                VerificationVerdict.FAIL,
                reasons=["TESTS_FAILED"],
                test=failure,
                retryable=True,
            ),
            verification_result(
                VerificationVerdict.PASS, test=TestExecution(exit_code=0)
            ),
        ],
    )
    provider.generate_turn.side_effect = [ModelTurn(text="first"), ModelTurn(text="second")]

    result = await loop.start("Fix the bug")

    assert result.state.status is AgentStatus.COMPLETED
    assert provider.generate_turn.await_count == 2
    assert result.report.retry_count == 1
    assert result.report.verification_attempts == 2
    feedback = "\n".join(
        message.get("content", "")
        for message in provider.generate_turn.call_args_list[1].args[0]
    )
    assert "TESTS_FAILED" in feedback
    assert "untrusted_test_output" in feedback
    assert '"repair_attempt":1' in feedback


async def test_retry_budget_stops_at_three_repairs(harness):
    _, _, _, provider, loop = harness
    failure = TestExecution(exit_code=1, stderr="still failing\n")
    attach_verifier(
        loop,
        [
            verification_result(
                VerificationVerdict.FAIL,
                reasons=["TESTS_FAILED"],
                test=failure,
                retryable=True,
            )
            for _ in range(4)
        ],
    )
    provider.generate_turn.side_effect = [
        ModelTurn(text="attempt 1"),
        ModelTurn(text="attempt 2"),
        ModelTurn(text="attempt 3"),
        ModelTurn(text="attempt 4"),
    ]

    result = await loop.start("Fix the bug")

    assert result.state.status is AgentStatus.FAILED
    assert result.state.error == "VERIFICATION_RETRIES_EXHAUSTED"
    assert provider.generate_turn.await_count == 4
    assert result.report.retry_count == 3
    assert result.report.verification_attempts == 4
    assert result.report.remaining_issues == ["TESTS_FAILED", "VERIFICATION_RETRIES_EXHAUSTED"]


async def test_unauthorized_changes_stop_without_retry(harness):
    _, _, _, provider, loop = harness
    attach_verifier(
        loop,
        [
            verification_result(
                VerificationVerdict.FAIL,
                reasons=["UNAUTHORIZED_CHANGES"],
                changes=[
                    FileChange(path="app.py", change="modified", authorized=False)
                ],
                retryable=True,
            )
        ],
    )
    provider.generate_turn.return_value = ModelTurn(text="Done")

    result = await loop.start("Fix the bug")

    assert result.state.status is AgentStatus.FAILED
    assert result.state.error == "UNAUTHORIZED_CHANGES"
    assert provider.generate_turn.await_count == 1
    assert result.report.verdict is VerificationVerdict.FAIL


async def test_policy_violation_stops_without_retry(harness):
    _, _, _, provider, loop = harness
    attach_verifier(
        loop,
        [
            verification_result(
                VerificationVerdict.FAIL,
                reasons=["SENSITIVE_CONTENT_DETECTED"],
                policy=["SENSITIVE_CONTENT_DETECTED"],
                retryable=True,
            )
        ],
    )
    provider.generate_turn.return_value = ModelTurn(text="Done")

    result = await loop.start("Fix the bug")

    assert result.state.status is AgentStatus.FAILED
    assert result.state.error == "POLICY_VIOLATION"
    assert provider.generate_turn.await_count == 1


async def test_inconclusive_verification_stops(harness):
    _, _, _, provider, loop = harness
    attach_verifier(
        loop,
        [
            verification_result(
                VerificationVerdict.INCONCLUSIVE,
                reasons=["SANDBOX_UNAVAILABLE"],
                test=TestExecution(error="SANDBOX_UNAVAILABLE"),
            )
        ],
    )
    provider.generate_turn.return_value = ModelTurn(text="Done")

    result = await loop.start("Fix the bug")

    assert result.state.status is AgentStatus.FAILED
    assert result.state.error == "VERIFICATION_INCONCLUSIVE"
    assert provider.generate_turn.await_count == 1
    assert result.report.verdict is VerificationVerdict.INCONCLUSIVE
    assert result.report.remaining_issues == [
        "SANDBOX_UNAVAILABLE",
        "VERIFICATION_INCONCLUSIVE",
    ]


async def test_applied_patch_hash_is_passed_to_verification(harness):
    _, _, permissions, provider, loop = harness
    verifier = attach_verifier(
        loop,
        [
            verification_result(
                VerificationVerdict.PASS, test=TestExecution(exit_code=0)
            )
        ],
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
                        "content": "value = 2\n",
                    },
                )
            ]
        ),
        ModelTurn(text="Done"),
    ]

    waiting = await loop.start("Change the value")
    assert waiting.state.status is AgentStatus.WAITING_APPROVAL
    assert verifier.calls == []
    permissions.approve(waiting.pending.action.action_id)
    result = await loop.resume()

    assert result.state.status is AgentStatus.COMPLETED
    assert verifier.calls[-1]["app.py"] == hashlib.sha256(b"value = 2\n").hexdigest()


async def test_report_includes_changes_when_task_fails_before_verification(harness):
    _, _, permissions, provider, loop = harness
    verifier = FakeVerifier(
        [],
        fallback_diff=DiffReport(
            changes=[FileChange(path="app.py", change="modified", authorized=True)]
        ),
    )
    loop.verifier = verifier
    provider.generate_turn.side_effect = [
        ModelTurn(
            tool_calls=[
                ToolCall(
                    id="patch-1",
                    name="apply_patch",
                    arguments={
                        "operation": "replace",
                        "target": "app.py",
                        "content": "value = 2\n",
                    },
                )
            ]
        ),
        ProviderError("PROVIDER_ERROR"),
    ]

    waiting = await loop.start("Change the value")
    permissions.approve(waiting.pending.action.action_id)
    result = await loop.resume()

    assert result.state.status is AgentStatus.FAILED
    assert result.state.error == "PROVIDER_ERROR"
    assert verifier.prepared == 1
    assert verifier.calls == []
    assert result.report.verdict is VerificationVerdict.INCONCLUSIVE
    assert [change.path for change in result.report.files_changed] == ["app.py"]
    assert result.report.tests_executed == []
