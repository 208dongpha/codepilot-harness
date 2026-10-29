from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class ToolError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class ToolResult(BaseModel):
    ok: bool
    data: dict[str, object] = Field(default_factory=dict)
    error: str | None = None


@dataclass(frozen=True)
class ReadOnlyTool:
    name: str
    arguments: type[BaseModel]
    execute: Callable
    read_only: Literal[True] = True
