from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from codepilot.agent.loop import AgentLoop
from codepilot.agent.state import AgentStatus
from codepilot.config import RepositoryConfig
from codepilot.context.builder import ContextBuilder
from codepilot.context.repository_map import RepositoryMapper
from codepilot.context.token_budget import TokenBudget
from codepilot.llm.base import ModelTurn, ToolCall
from codepilot.patch.tool import IsolatedWorkspace, PatchTool
from codepilot.permissions.engine import PermissionEngine
from codepilot.tools.factory import create_read_only_registry
from codepilot.tools.workspace import Workspace


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
    from codepilot.llm.base import ProviderError

    _, _, _, provider, loop = harness
    provider.generate_turn.side_effect = ProviderError("RATE_LIMIT_ERROR")
    result = await loop.start("Read code")
    assert result.state.status is AgentStatus.FAILED
    assert result.state.error == "RATE_LIMIT_ERROR"
