import pytest
from pydantic import Field

from codepilot.tools.base import ReadOnlyTool, ToolArguments, ToolError
from codepilot.tools.registry import ToolRegistry


class Args(ToolArguments):
    path: str = Field(min_length=1)


def test_registry_validates_and_rejects_unknown():
    registry = ToolRegistry()
    registry.register(ReadOnlyTool("read_file", Args, lambda args: {"path": args.path}))
    assert registry.names == ("read_file",)
    assert registry.call("read_file", {"path": "a.py"}).data == {"path": "a.py"}
    assert registry.call("nope", {}).error == "UNKNOWN_TOOL"
    for args in ({}, {"path": 1}, {"path": "x", "extra": True}):
        assert registry.call("read_file", args).error == "INVALID_ARGUMENTS"
    with pytest.raises(ValueError):
        registry.register(ReadOnlyTool("read_file", Args, lambda _: {}))
    with pytest.raises(ValueError):
        registry.register(ReadOnlyTool("apply_patch", Args, lambda _: {}))
    with pytest.raises(ValueError):
        registry.register(ReadOnlyTool("list_files", Args, lambda _: {}, read_only=False))


def test_errors_are_structured_and_do_not_echo_arguments():
    def fail(args):
        raise ToolError("ACCESS_DENIED")

    registry = ToolRegistry()
    registry.register(ReadOnlyTool("read_file", Args, fail))
    result = registry.call("read_file", {"path": "synthetic-private-value"})
    assert result.error == "ACCESS_DENIED"
    assert "synthetic-private-value" not in result.model_dump_json()


def test_output_limit_and_unexpected_error():
    registry = ToolRegistry(max_output_chars=256)
    registry.register(ReadOnlyTool("read_file", Args, lambda _: {"content": "x" * 1000}))
    assert registry.call("read_file", {"path": "x"}).error == "OUTPUT_LIMIT"

    def fail(args):
        raise RuntimeError("private-error")

    registry.register(ReadOnlyTool("list_files", Args, fail))
    assert registry.call("list_files", {"path": "x"}).error == "TOOL_ERROR"
