import logging

from pydantic import ValidationError

from codepilot.tools.base import ReadOnlyTool, ToolError, ToolResult

logger = logging.getLogger("codepilot")

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

    @property
    def declarations(self) -> list[dict[str, object]]:
        return [
            {
                "name": name,
                "description": f"Read-only repository tool: {name}",
                "parameters": self._tools[name].arguments.model_json_schema(),
            }
            for name in sorted(self._tools)
        ]

    def call(self, name: str, arguments: dict[str, object]) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            logger.warning("tool_error UNKNOWN_TOOL")
            return ToolResult(ok=False, error="UNKNOWN_TOOL")
        logger.info("tool_call %s", name)
        try:
            validated = tool.arguments.model_validate(arguments)
        except ValidationError:
            logger.warning("tool_error INVALID_ARGUMENTS")
            return ToolResult(ok=False, error="INVALID_ARGUMENTS")
        try:
            result = ToolResult(ok=True, data=tool.execute(validated))
        except ToolError as exc:
            logger.warning("tool_error EXECUTION_ERROR")
            return ToolResult(ok=False, error=exc.code)
        except Exception:
            logger.warning("tool_error EXECUTION_ERROR")
            return ToolResult(ok=False, error="TOOL_ERROR")
        if len(result.model_dump_json()) > self.max_output_chars:
            logger.warning("tool_error OUTPUT_LIMIT")
            return ToolResult(ok=False, error="OUTPUT_LIMIT")
        logger.info("tool_success %s", name)
        return result
