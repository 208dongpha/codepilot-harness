from pydantic import ValidationError

from codepilot.tools.base import ReadOnlyTool, ToolError, ToolResult

READ_ONLY_NAMES = frozenset({"list_files", "read_file", "search_code"})


class ToolRegistry:
    def __init__(self, max_output_chars: int = 32000) -> None:
        if max_output_chars < 256:
            raise ValueError("Output budget must be at least 256 characters.")
        self.max_output_chars = max_output_chars
        self._tools: dict[str, ReadOnlyTool] = {}

    def register(self, tool: ReadOnlyTool) -> None:
        if tool.name not in READ_ONLY_NAMES or tool.read_only is not True:
            raise ValueError("Only Phase 2 read-only tools can be registered.")
        if tool.name in self._tools:
            raise ValueError("Tool already registered.")
        self._tools[tool.name] = tool

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))

    def call(self, name: str, arguments: dict[str, object]) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(ok=False, error="UNKNOWN_TOOL")
        try:
            validated = tool.arguments.model_validate(arguments)
        except ValidationError:
            return ToolResult(ok=False, error="INVALID_ARGUMENTS")
        try:
            result = ToolResult(ok=True, data=tool.execute(validated))
        except ToolError as exc:
            return ToolResult(ok=False, error=exc.code)
        except Exception:
            return ToolResult(ok=False, error="TOOL_ERROR")
        if len(result.model_dump_json()) > self.max_output_chars:
            return ToolResult(ok=False, error="OUTPUT_LIMIT")
        return result
