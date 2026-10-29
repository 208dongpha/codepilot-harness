from typing import Protocol, TypedDict


class Message(TypedDict):
    role: str
    content: str


class ProviderError(RuntimeError):
    """Safe error code; never contains the provider response or credentials."""


class LLMProvider(Protocol):
    async def generate(self, messages: list[Message]) -> str: ...
