import asyncio
import logging

import typer

from codepilot import __version__
from codepilot.cli.renderer import show
from codepilot.config import Config, ConfigurationError, display_model, load_config
from codepilot.execution.docker_runtime import DockerError, DockerRuntime
from codepilot.llm.base import Message, ProviderError
from codepilot.llm.gemini import GeminiProvider
from codepilot.telemetry.logging import configure_logging

app = typer.Typer(invoke_without_command=True)
sandbox = typer.Typer()
app.add_typer(sandbox, name="sandbox")
logger = logging.getLogger("codepilot")


async def chat(config: Config) -> None:
    provider = GeminiProvider(config)
    history: list[Message] = []
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
                show("/help - Commands\n/status - Session status\n/exit - Exit")
            elif text == "/status":
                available = DockerRuntime(config.sandbox).available()
                show(
                    f"Provider: {config.llm.provider}\nModel: {display_model(config.llm.model)}\n"
                    "Session: In-memory\n"
                    f"Sandbox: Docker {'Available' if available else 'Unavailable'}"
                )
            elif text.startswith("/"):
                show("Unknown command. Type /help.")
            else:
                pending = [*history, Message(role="user", content=text)]
                try:
                    response = await provider.generate(pending)
                except ProviderError as exc:
                    show(f"Error: {exc}")
                    continue
                history = [*pending, Message(role="assistant", content=response)]
                show(f"Assistant> {response}")
    finally:
        await provider.close()
        show("Goodbye!")


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
