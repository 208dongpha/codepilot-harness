import os
from unittest.mock import patch

import pytest

from codepilot.config import RepositoryConfig, load_config
from codepilot.tools.base import ToolError
from codepilot.tools.files import FileTools
from codepilot.tools.registry import ToolRegistry
from codepilot.tools.workspace import Workspace


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "main.py").write_text("class Example:\n    pass\n", encoding="utf-8")
    return Workspace(RepositoryConfig(workspace=root))


def registry(workspace):
    result = ToolRegistry(workspace.config.max_output_chars)
    for tool in FileTools(workspace).tools():
        result.register(tool)
    return result


def test_valid_listing_and_read(workspace):
    tools = registry(workspace)
    assert tools.call("list_files", {}).data == {"paths": ["main.py"], "truncated": False}
    result = tools.call("read_file", {"path": "main.py", "start_line": 2, "max_lines": 1})
    assert result.ok and result.data["content"] == "    pass\n"
    assert result.data["start_line"] == 2
    assert not result.data["truncated"]


@pytest.mark.parametrize(
    "path",
    [
        "../outside.txt",
        "sub/../../outside.txt",
        "..\\outside.txt",
        "/etc/passwd",
        "C:\\Windows\\file",
        "C:relative.txt",
        "\\\\server\\share",
        "main.py:stream",
        "main.py\x00",
        "main.py\n",
    ],
)
def test_path_traversal_and_absolute_paths_rejected(workspace, path):
    assert registry(workspace).call("read_file", {"path": path}).error == "ACCESS_DENIED"


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        ".env.example",
        "api_key.md",
        ".git/config",
        ".venv/code.py",
        "__pycache__/code.py",
        "secrets.yaml",
        "keys/private.pem",
    ],
)
def test_sensitive_paths_excluded(workspace, path):
    target = workspace.root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("synthetic-value")
    tools = registry(workspace)
    assert path not in tools.call("list_files", {}).data["paths"]
    assert tools.call("read_file", {"path": path}).error == "ACCESS_DENIED"


def test_sensitive_content_not_returned(workspace):
    (workspace.root / "settings.txt").write_text('password = "synthetic-password-value"')
    result = registry(workspace).call("read_file", {"path": "settings.txt"})
    assert result.error == "SENSITIVE_CONTENT"
    assert "synthetic-password-value" not in result.model_dump_json()


def test_binary_encoding_large_and_missing_files(workspace):
    tools = registry(workspace)
    for name, content, error in [
        ("binary.bin", b"abc\x00", "BINARY_FILE"),
        ("encoding.txt", b"\xff\xfe", "ENCODING_ERROR"),
        ("large.txt", b"x" * (workspace.config.max_file_bytes + 1), "FILE_TOO_LARGE"),
    ]:
        (workspace.root / name).write_bytes(content)
        assert tools.call("read_file", {"path": name}).error == error
    assert tools.call("read_file", {"path": "missing.py"}).error == "NOT_FOUND"
    assert (
        tools.call("read_file", {"path": "main.py", "max_lines": "2"}).error == "INVALID_ARGUMENTS"
    )


def test_permission_error_is_safe(workspace):
    with patch("codepilot.tools.workspace.os.open", side_effect=PermissionError("private-value")):
        result = registry(workspace).call("read_file", {"path": "main.py"})
    assert result.error == "PERMISSION_DENIED"
    assert "private-value" not in result.model_dump_json()


def test_symlink_escape_and_directory_symlink_not_followed(workspace, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "data.txt").write_text("synthetic-outside-value")
    try:
        (workspace.root / "alias.txt").symlink_to(outside / "data.txt")
        (workspace.root / "alias-dir").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation not permitted by OS")
    tools = registry(workspace)
    for path in ("alias.txt", "alias-dir/data.txt"):
        assert tools.call("read_file", {"path": path}).error == "ACCESS_DENIED"
    assert tools.call("list_files", {}).data["paths"] == ["main.py"]


def test_hardlink_cannot_alias_outside_file(workspace, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("synthetic-outside-value")
    try:
        os.link(outside, workspace.root / "alias.txt")
    except OSError:
        pytest.skip("Hard links not supported by OS")
    assert registry(workspace).call("read_file", {"path": "alias.txt"}).error == "ACCESS_DENIED"


def test_limits_and_no_writes(workspace):
    (workspace.root / "long.txt").write_text("line\n" * 2000)
    workspace.config.max_output_chars = 512
    before = {p.name: p.read_bytes() for p in workspace.root.iterdir()}
    tools = registry(workspace)
    result = tools.call("read_file", {"path": "long.txt"})
    assert result.ok and result.data["truncated"]
    assert len(result.model_dump_json()) <= 512
    assert tools.call("list_files", {"limit": 1}).data["truncated"]
    workspace.config.max_scan_entries = 1
    assert tools.call("list_files", {}).data["truncated"]
    assert before == {p.name: p.read_bytes() for p in workspace.root.iterdir()}


def test_repository_config_overrides(monkeypatch):
    monkeypatch.setenv("CODEPILOT_REPOSITORY_MAX_CONTEXT_CHARS", "1024")
    assert load_config().repository.max_context_chars == 1024
    monkeypatch.setenv("CODEPILOT_REPOSITORY_MAX_FILE_BYTES", "0")
    from codepilot.config import ConfigurationError

    with pytest.raises(ConfigurationError):
        load_config()


def test_invalid_workspace(tmp_path):
    with pytest.raises(ToolError, match="INVALID_WORKSPACE"):
        Workspace(RepositoryConfig(workspace=tmp_path / "absent"))
