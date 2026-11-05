from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from typer.testing import CliRunner

from codepilot.agent.loop import AgentRunResult
from codepilot.agent.state import AgentState, AgentStatus
from codepilot.cli.main import app
from codepilot.patch.tool import PatchProposal
from codepilot.permissions.engine import PermissionEngine
from codepilot.verification.models import (
    DiffReport,
    FileChange,
    TestExecution,
    VerificationResult,
    VerificationVerdict,
)
from codepilot.verification.report import TaskReport

runner = CliRunner()


def test_help():
    assert runner.invoke(app, ["--help"]).exit_code == 0


def test_commands():
    with patch("codepilot.cli.main.GeminiProvider") as factory:
        factory.return_value.close = AsyncMock()
        with patch("codepilot.cli.main.DockerRuntime.available", return_value=False):
            result = runner.invoke(app, input="/help\n/status\n/nope\n\n/exit\n")
        assert result.exit_code == 0
        assert "Unknown command" in result.output
        assert "In-memory" in result.output
        assert "Unavailable" in result.output
        assert "Goodbye!" in result.output
        factory.return_value.generate.assert_not_called()


def test_agent_results_are_rendered():
    with patch("codepilot.cli.main.GeminiProvider") as factory:
        provider = factory.return_value
        provider.close = AsyncMock()
        failed = Mock(
            state=AgentState(status=AgentStatus.FAILED, error="RATE_LIMIT_ERROR"),
            start=AsyncMock(
                return_value=AgentRunResult(
                    state=AgentState(status=AgentStatus.FAILED, error="RATE_LIMIT_ERROR")
                )
            ),
        )
        completed = Mock(
            state=AgentState(status=AgentStatus.COMPLETED),
            start=AsyncMock(
                return_value=AgentRunResult(
                    state=AgentState(status=AgentStatus.COMPLETED), response="done"
                )
            ),
        )
        with patch("codepilot.cli.main._create_agent_loop", side_effect=[failed, completed]):
            result = runner.invoke(app, input="failed\nhello\n/exit\n")
        assert result.exit_code == 0
        assert "RATE_LIMIT_ERROR" in result.output
        assert "Assistant> done" in result.output
        provider.close.assert_awaited_once()


def test_cli_approval_and_denial_commands_show_exact_patch():
    proposal = PatchProposal(
        operation="replace",
        target="app.py",
        expected_sha256="a" * 64,
        content="new\n",
        diff="--- a/app.py\n+++ b/app.py\n",
    )
    permissions = PermissionEngine()
    permissions.request(proposal.action)
    waiting_state = AgentState(status=AgentStatus.WAITING_APPROVAL)
    completed_state = AgentState(status=AgentStatus.COMPLETED)
    loop = SimpleNamespace(
        state=waiting_state,
        permissions=permissions,
        start=AsyncMock(return_value=AgentRunResult(state=waiting_state, pending=proposal)),
        resume=AsyncMock(return_value=AgentRunResult(state=completed_state, response="applied")),
    )
    with patch("codepilot.cli.main.GeminiProvider") as factory:
        factory.return_value.close = AsyncMock()
        with patch("codepilot.cli.main._create_agent_loop", return_value=loop):
            result = runner.invoke(app, input="change app\n/approve\n/exit\n")
    assert result.exit_code == 0
    assert proposal.action.action_id in result.output
    assert "Target: app.py" in result.output
    assert "Assistant> applied" in result.output
    loop.resume.assert_awaited_once()


def test_eof():
    assert runner.invoke(app, input="").exit_code == 0


def test_invalid_config():
    from pathlib import Path

    Path("codepilot.toml").write_text("invalid[")
    assert runner.invoke(app, input="/exit\n").exit_code == 1


def test_keyboard_interrupt_cleanup():
    with patch("codepilot.cli.main.GeminiProvider") as factory:
        factory.return_value.close = AsyncMock()
        with patch("builtins.input", side_effect=KeyboardInterrupt):
            result = runner.invoke(app)
        assert result.exit_code == 0
        factory.return_value.close.assert_awaited_once()


def test_model_credential_not_printed(monkeypatch):
    marker = "AQ." + "synthetic" * 6
    monkeypatch.setenv("CODEPILOT_LLM_MODEL", marker)
    result = runner.invoke(app, input="/status\n/exit\n")
    assert result.exit_code == 1
    assert marker not in result.output
    assert "Invalid configuration" in result.output


def test_verification_report_is_rendered():
    test = TestExecution(
        command=["python", "-m", "pytest"],
        exit_code=1,
        stderr="assertion failed\n",
        duration_seconds=0.5,
    )
    verification = VerificationResult(
        verdict=VerificationVerdict.FAIL,
        reasons=["TESTS_FAILED"],
        test=test,
        diff=DiffReport(
            changes=[FileChange(path="app.py", change="modified", authorized=True)]
        ),
        retryable=True,
    )
    report = TaskReport(
        verdict=VerificationVerdict.FAIL,
        files_changed=verification.diff.changes,
        tests_executed=[test],
        retry_count=3,
        verification_attempts=4,
        remaining_issues=["TESTS_FAILED", "VERIFICATION_RETRIES_EXHAUSTED"],
    )
    failed = Mock(
        state=AgentState(status=AgentStatus.FAILED, error="VERIFICATION_RETRIES_EXHAUSTED"),
        start=AsyncMock(
            return_value=AgentRunResult(
                state=AgentState(
                    status=AgentStatus.FAILED, error="VERIFICATION_RETRIES_EXHAUSTED"
                ),
                verification=verification,
                verifications=[verification],
                report=report,
            )
        ),
    )
    with patch("codepilot.cli.main.GeminiProvider") as factory:
        factory.return_value.close = AsyncMock()
        with patch("codepilot.cli.main._create_agent_loop", return_value=failed):
            result = runner.invoke(app, input="fix\n/exit\n")
    assert result.exit_code == 0
    assert "Verification: FAIL" in result.output
    assert "Tests executed: 1" in result.output
    assert "Repair retries: 3" in result.output
    assert "app.py (modified, authorized)" in result.output
    assert "assertion failed" in result.output


def _terminal_report(reason):
    return AgentRunResult(
        state=AgentState(status=AgentStatus.CANCELLED, error=reason),
        report=TaskReport(
            verdict=VerificationVerdict.INCONCLUSIVE,
            remaining_issues=[reason],
        ),
    )


def _waiting_loop(result):
    waiting = AgentState(status=AgentStatus.WAITING_APPROVAL)
    return SimpleNamespace(
        state=waiting,
        permissions=Mock(pending=None),
        start=AsyncMock(return_value=AgentRunResult(state=waiting)),
        deny=Mock(return_value=result),
        cancel=Mock(return_value=result),
    )


def test_deny_emits_inconclusive_task_report():
    loop = _waiting_loop(_terminal_report("PENDING_ACTION_DENIED"))
    with patch("codepilot.cli.main.GeminiProvider") as factory:
        factory.return_value.close = AsyncMock()
        with patch("codepilot.cli.main._create_agent_loop", return_value=loop):
            result = runner.invoke(app, input="task\n/deny\n/exit\n")
    assert result.exit_code == 0
    assert "Pending action denied. No files were changed." in result.output
    assert "Verification: INCONCLUSIVE" in result.output
    assert "Remaining issues: PENDING_ACTION_DENIED" in result.output
    loop.deny.assert_called_once()
    loop.cancel.assert_not_called()


def test_cancel_emits_inconclusive_task_report():
    loop = _waiting_loop(_terminal_report("TASK_CANCELLED"))
    with patch("codepilot.cli.main.GeminiProvider") as factory:
        factory.return_value.close = AsyncMock()
        with patch("codepilot.cli.main._create_agent_loop", return_value=loop):
            result = runner.invoke(app, input="task\n/cancel\n/exit\n")
    assert result.exit_code == 0
    assert "Task cancelled." in result.output
    assert "Verification: INCONCLUSIVE" in result.output
    assert "Remaining issues: TASK_CANCELLED" in result.output
    loop.cancel.assert_called_once()
    loop.deny.assert_not_called()
