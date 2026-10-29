"""Configuration: process environment > .env > TOML > defaults."""

import os
import re
import tomllib
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError


class ConfigurationError(ValueError):
    pass


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


MODEL_PATTERN = r"(?:YOUR_SUPPORTED_GEMINI_MODEL_ID|(?:models/)?gemini-[a-z0-9]+(?:[.-][a-z0-9]+)*)"


def display_model(value: str) -> str:
    return value if re.fullmatch(MODEL_PATTERN, value) else "[REDACTED]"


class LLMConfig(Section):
    provider: Literal["gemini"] = "gemini"
    model: str = Field(
        default="YOUR_SUPPORTED_GEMINI_MODEL_ID",
        pattern=rf"^{MODEL_PATTERN}$",
        repr=False,
    )
    temperature: float = Field(default=0.2, ge=0, le=2)
    timeout_seconds: float = Field(default=60, gt=0)


class AgentConfig(Section):
    max_steps: int = Field(default=20, gt=0)
    max_verification_retries: int = Field(default=3, ge=0, le=3)


class PermissionsConfig(Section):
    mode: Literal["ask_before_write"] = "ask_before_write"


class SandboxConfig(Section):
    enabled: Literal[True] = True
    engine: Literal["docker"] = "docker"
    network_enabled: Literal[False] = False
    timeout_seconds: int = Field(default=60, gt=0)


class LoggingConfig(Section):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"


class Config(Section):
    llm: LLMConfig = Field(default_factory=LLMConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    permissions: PermissionsConfig = Field(default_factory=PermissionsConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    api_key: SecretStr | None = Field(default=None, exclude=True, repr=False)


def load_config(path: Path = Path("codepilot.toml")) -> Config:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if "api_key" in data:
            raise ConfigurationError("API keys must be configured through environment or .env.")
        env = {**dotenv_values(path.parent / ".env"), **os.environ}
        for section, model in (
            ("llm", LLMConfig),
            ("agent", AgentConfig),
            ("permissions", PermissionsConfig),
            ("sandbox", SandboxConfig),
            ("logging", LoggingConfig),
        ):
            for field in model.model_fields:
                name = f"CODEPILOT_{section}_{field}".upper()
                if name in env:
                    data.setdefault(section, {})[field] = env[name]
        # Pydantic Literal booleans require actual bools for environment overrides.
        for field in ("enabled", "network_enabled"):
            value = data.get("sandbox", {}).get(field)
            if isinstance(value, str) and value.lower() in ("true", "false"):
                data["sandbox"][field] = value.lower() == "true"
        data["api_key"] = env.get("GEMINI_API_KEY") or None
        return Config.model_validate(data)
    except (OSError, ValueError, TypeError, AttributeError, ValidationError) as exc:
        if isinstance(exc, ConfigurationError):
            raise
        raise ConfigurationError(
            "Invalid configuration. Check TOML and environment overrides."
        ) from None
