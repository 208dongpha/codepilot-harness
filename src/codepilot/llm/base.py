from typing import Any, Protocol, TypedDict

from pydantic import BaseModel, ConfigDict, Field


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    id: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=64)
    arguments: dict[str, Any]
    thought_signature: bytes | None = Field(default=None, exclude=True)


class ModelTurn(BaseModel):
    text: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list, max_length=8)


class Message(TypedDict, total=False):
    role: str
    content: str
    name: str
    call_id: str
    tool_calls: list[ToolCall]


class ProviderError(RuntimeError):
    """Safe error code; never contains the provider response or credentials."""


class LLMProvider(Protocol):
    async def generate(self, messages: list[Message]) -> str: ...

    async def generate_turn(
        self, messages: list[Message], tools: list[dict[str, object]]
    ) -> ModelTurn: ...

    async def count_tokens(
        self, messages: list[Message], tools: list[dict[str, object]]
    ) -> int | None: ...
