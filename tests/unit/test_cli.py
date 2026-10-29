from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

from codepilot.cli.main import app
from codepilot.llm.base import ProviderError

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


def test_history_and_provider_failure():
    with patch("codepilot.cli.main.GeminiProvider") as factory:
        provider = factory.return_value
        provider.close = AsyncMock()
        provider.generate = AsyncMock(side_effect=[ProviderError("RATE_LIMIT_ERROR"), "one", "two"])
        result = runner.invoke(app, input="failed\nhello\nagain\n/exit\n")
        assert result.exit_code == 0
        assert "RATE_LIMIT_ERROR" in result.output
        messages = provider.generate.call_args_list[-1].args[0]
        assert messages == [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "one"},
            {"role": "user", "content": "again"},
        ]
        provider.close.assert_awaited_once()


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
