from pathlib import Path

import pytest

from codepilot.config import ConfigurationError, load_config


def test_load_valid_config():
    Path("codepilot.toml").write_text('[llm]\nmodel="gemini-test"')
    assert load_config().llm.model == "gemini-test"


def test_environment_override(monkeypatch):
    Path(".env").write_text("GEMINI_API_KEY=fixture-secret\nCODEPILOT_LLM_MODEL=gemini-dotenv")
    monkeypatch.setenv("CODEPILOT_LLM_MODEL", "gemini-env")
    config = load_config()
    assert config.llm.model == "gemini-env"
    assert config.api_key.get_secret_value() == "fixture-secret"
    assert "fixture-secret" not in repr(config)
    assert "fixture-secret" not in config.model_dump_json()


@pytest.mark.parametrize(
    "content",
    [
        "broken [",
        '[llm]\nmodel=""',
        "[sandbox]\nnetwork_enabled=true",
        "[agent]\nmax_steps=0",
        'api_key="secret"',
    ],
)
def test_invalid_config(content):
    Path("codepilot.toml").write_text(content)
    with pytest.raises(ConfigurationError):
        load_config()


def test_missing_api_key():
    assert load_config().api_key is None


def test_all_environment_overrides(monkeypatch):
    monkeypatch.setenv("CODEPILOT_LLM_TEMPERATURE", "0.5")
    monkeypatch.setenv("CODEPILOT_SANDBOX_TIMEOUT_SECONDS", "15")
    monkeypatch.setenv("CODEPILOT_LOGGING_LEVEL", "DEBUG")
    monkeypatch.setenv("CODEPILOT_SANDBOX_NETWORK_ENABLED", "false")
    config = load_config()
    assert config.llm.temperature == 0.5
    assert config.sandbox.timeout_seconds == 15
    assert config.logging.level == "DEBUG"


def test_invalid_override_has_no_secret(monkeypatch):
    monkeypatch.setenv("CODEPILOT_LLM_TEMPERATURE", "private-value")
    with pytest.raises(ConfigurationError) as caught:
        load_config()
    assert "private-value" not in str(caught.value)


@pytest.mark.parametrize(
    "model",
    [
        "AQ." + "x" * 40,
        "AIza" + "x" * 35,
        "Bearer synthetic-value",
        "gemini-test\n",
        "gemini-test?key=synthetic-value",
    ],
)
def test_credential_like_model_rejected_without_echo(model, monkeypatch):
    from codepilot.config import display_model

    monkeypatch.setenv("CODEPILOT_LLM_MODEL", model)
    with pytest.raises(ConfigurationError) as caught:
        load_config()
    assert model not in str(caught.value)
    assert display_model(model) == "[REDACTED]"


@pytest.mark.parametrize("model", ["gemini-3.8-flash", "models/gemini-2.5-flash"])
def test_model_identifier_display(model):
    from codepilot.config import LLMConfig, display_model

    assert display_model(LLMConfig(model=model).model) == model
