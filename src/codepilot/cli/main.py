import asyncio
import logging

import typer

from codepilot import __version__
from codepilot.agent.loop import AgentLoop, AgentRunResult
from codepilot.agent.state import AgentStatus
from codepilot.cli.renderer import show
from codepilot.config import Config, ConfigurationError, display_model, load_config
from codepilot.context.builder import ContextBuilder
from codepilot.context.repository_map import RepositoryMapper
from codepilot.context.token_budget import TokenBudget
from codepilot.execution.docker_runtime import DockerError, DockerRuntime
from codepilot.llm.gemini import GeminiProvider
from codepilot.patch.tool import IsolatedWorkspace, PatchTool
from codepilot.permissions.engine import PermissionEngine, PermissionError
from codepilot.telemetry.logging import configure_logging
from codepilot.tools.factory import create_read_only_registry
from codepilot.tools.workspace import Workspace
from codepilot.verification.diff import GitDiffVerifier
from codepilot.verification.engine import VerificationEngine
from codepilot.verification.redaction import bound_and_redact
from codepilot.verification.report import TaskReport
from codepilot.verification.test_runner import DockerTestRunner

app = typer.Typer(invoke_without_command=True)
sandbox = typer.Typer()
app.add_typer(sandbox, name="sandbox")
logger = logging.getLogger("codepilot")


async def chat(config: Config) -> None:
    provider = GeminiProvider(config)
    workspace = Workspace(config.repository)
    isolated = IsolatedWorkspace.create(workspace)
    current: AgentLoop | None = None
    show(
        f"CodePilot Harness v{__version__}\nProvider: Gemini\n"
        f"Model: {display_model(config.llm.model)}"
    )
    show("Type /help to see available commands.")
    try:
        while True:
            try:
                text = input("codepilot> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not text:
                continue
            if text == "/exit":
                break
            if text == "/help":
                show(
                    "/help - Commands\n/status - Session status\n"
                    "/approve - Approve the exact pending patch\n"
                    "/deny - Deny the pending patch\n/cancel - Cancel the active task\n/exit - Exit"
                )
            elif text == "/status":
                available = DockerRuntime(config.sandbox).available()
                show(
                    f"Provider: {config.llm.provider}\nModel: {display_model(config.llm.model)}\n"
                    "Session: In-memory\n"
                    f"Sandbox: Docker {'Available' if available else 'Unavailable'}"
                )
            elif text == "/approve":
                if current is None or current.state.status is not AgentStatus.WAITING_APPROVAL:
                    show("No pending action.")
                    continue
                pending = current.permissions.pending
                try:
                    if pending is None:
                        raise PermissionError("APPROVAL_MISMATCH")
                    current.permissions.approve(pending.action.action_id)
                    result = await current.resume()
                except PermissionError as exc:
                    show(f"Error: {exc}")
                    continue
                _show_agent_result(result)
                if result.state.status is not AgentStatus.WAITING_APPROVAL:
                    current = None
            elif text == "/deny":
                if current is None or current.state.status is not AgentStatus.WAITING_APPROVAL:
                    show("No pending action.")
                    continue
                pending = current.permissions.pending
                if pending is not None:
                    current.permissions.deny(pending.action.action_id)
                current.deny()
                show("Pending action denied. No files were changed.")
                current = None
            elif text == "/cancel":
                if current is None:
                    show("No active task.")
                    continue
                current.cancel()
                show("Task cancelled.")
                current = None
            elif text.startswith("/"):
                show("Unknown command. Type /help.")
            else:
                if current is not None:
                    show("Resolve the pending action with /approve, /deny, or /cancel.")
                    continue
                current = _create_agent_loop(config, provider, workspace, isolated)
                result = await current.start(text)
                _show_agent_result(result)
                if result.state.status is not AgentStatus.WAITING_APPROVAL:
                    current = None
    finally:
        await provider.close()
        isolated.close()
        show("Goodbye!")


def _create_agent_loop(
    config: Config,
    provider: GeminiProvider,
    workspace: Workspace,
    isolated: IsolatedWorkspace,
) -> AgentLoop:
    permissions = PermissionEngine()
    isolated_config = config.repository.model_copy(update={"workspace": isolated.root})
    isolated_workspace = Workspace(isolated_config)
    verifier = VerificationEngine(
        workspace=isolated_workspace,
        runner=DockerTestRunner(config.verification, config.sandbox),
        differ=GitDiffVerifier(
            isolated_workspace,
            config.repository.max_total_bytes,
            config.repository.max_output_chars,
        ),
        max_total_bytes=config.repository.max_total_bytes,
    )
    return AgentLoop(
        provider=provider,
        registry=create_read_only_registry(isolated_config),
        mapper=RepositoryMapper(workspace),
        context_builder=ContextBuilder(workspace),
        permissions=permissions,
        patch_tool=PatchTool(isolated, permissions, config.repository.max_file_bytes),
        token_budget=TokenBudget(
            config.agent.max_input_tokens,
            config.agent.reserved_output_tokens,
            provider.count_tokens,
        ),
        max_steps=config.agent.max_steps,
        verifier=verifier,
        max_verification_retries=config.agent.max_verification_retries,
    )


def _show_agent_result(result: AgentRunResult) -> None:
    if result.state.status is AgentStatus.WAITING_APPROVAL and result.pending is not None:
        action = result.pending.action
        show(
            "Approval required\n"
            f"Action ID: {action.action_id}\n"
            f"Operation: {action.operation}\nTarget: {action.target}\n"
            f"Proposed changes:\n{result.pending.diff}\n"
            "Use /approve, /deny, or /cancel."
        )
    elif result.state.status is AgentStatus.COMPLETED:
        show(f"Assistant> {result.response or ''}")
    elif result.state.status is AgentStatus.FAILED:
        show(f"Error: {result.state.error or 'AGENT_ERROR'}")
    if result.report is not None:
        show(_format_report(result.report))


def _format_report(report: TaskReport) -> str:
    lines = [
        f"Verification: {report.verdict.value}",
        f"Files changed: {len(report.files_changed)}",
        f"Tests executed: {len(report.tests_executed)}",
        f"Repair retries: {report.retry_count}",
    ]
    for change in report.files_changed[:10]:
        lines.append(
            f"- {change.path} ({change.change}, "
            f"{'authorized' if change.authorized else 'UNAUTHORIZED'})"
        )
    if report.remaining_issues:
        lines.append("Remaining issues: " + ", ".join(report.remaining_issues))
    test = report.tests_executed[-1] if report.tests_executed else None
    if test is not None:
        outcome = "TIMEOUT" if test.timed_out else f"exit {test.exit_code}"
        lines.append(f"Last test run: {outcome} in {test.duration_seconds:.2f}s")
        if report.verdict.value != "PASS":
            evidence = (test.stderr or test.stdout).strip()
            if evidence:
                excerpt, _, _ = bound_and_redact(evidence, 1200)
                lines.append(f"Test evidence:\n{excerpt}")
    return "\n".join(lines)


@app.callback()
def main(ctx: typer.Context) -> None:
    configure_logging()
    if ctx.invoked_subcommand:
        return
    try:
        config = load_config()
        configure_logging(config.logging.level)
        logger.info("application_startup")
        asyncio.run(chat(config))
    except ConfigurationError as exc:
        logger.error("configuration_error")
        show(str(exc))
        raise typer.Exit(1) from None
    except KeyboardInterrupt:
        show("Goodbye!")
    except Exception:
        logger.error("unexpected_exception")
        show("Unexpected application error. See safe event logs.")
        raise typer.Exit(1) from None


@sandbox.command("smoke-test")
def smoke_test() -> None:
    try:
        config = load_config()
        runtime = DockerRuntime(config.sandbox)
        result = runtime.smoke_test()
        show(
            f"Docker: Available\nSandbox image: Ready\nNetwork: Disabled\n"
            f"User: Non-root\n{result.stdout.strip()}\nSmoke test: PASS"
        )
    except (ConfigurationError, DockerError) as exc:
        logger.error("docker_smoke_or_configuration_error")
        show(f"Smoke test: FAIL - {exc}")
        raise typer.Exit(1) from None
