import pytest

from codepilot.config import RepositoryConfig
from codepilot.context.repository_map import RepositoryMapper
from codepilot.tools.workspace import Workspace


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "auth.py").write_text(
        "raise RuntimeError('must not execute')\n"
        "class Login:\n    def authenticate(self):\n        pass\n"
        "async def refresh():\n    pass\n"
    )
    return Workspace(RepositoryConfig(workspace=tmp_path))


def test_ast_packages_classes_and_functions_without_execution(workspace):
    result = RepositoryMapper(workspace).build()
    assert [entry.path for entry in result.modules] == ["pkg/__init__.py", "pkg/auth.py"]
    assert result.modules[0].package
    assert not result.modules[1].package
    symbols = result.modules[1].symbols
    assert [(s.name, s.line) for s in symbols] == [
        ("Login", 2),
        ("Login.authenticate", 3),
        ("refresh", 5),
    ]


def test_invalid_syntax_sensitive_binary_and_large_skipped(workspace):
    for name, data in [
        ("invalid.py", b"def broken(:"),
        ("binary.py", b"\x00"),
        ("private.py", b'password="synthetic-sensitive-value"'),
        ("large.py", b"x" * (workspace.config.max_file_bytes + 1)),
    ]:
        (workspace.root / name).write_bytes(data)
    (workspace.root / ".env.py").write_text("class Hidden: pass")
    result = RepositoryMapper(workspace).build()
    assert result.syntax_errors == 1 and result.skipped == 3
    assert all("Hidden" != s.name for m in result.modules for s in m.symbols)


def test_map_budgets_symbol_count_and_depth(workspace):
    workspace.config.max_map_chars = 256
    result = RepositoryMapper(workspace).build()
    assert len(result.model_dump_json()) <= 256 and result.truncated
    workspace.config.max_map_chars = 16000
    workspace.config.max_symbols = 1
    result = RepositoryMapper(workspace).build()
    assert sum(len(m.symbols) for m in result.modules) <= 1
    assert result.truncated
    workspace.config.max_depth = 1
    assert Workspace(workspace.config).list_files().truncated
    assert Workspace(workspace.config).list_files().paths == []


def test_total_read_budget(workspace):
    workspace.config.max_total_bytes = 1
    assert RepositoryMapper(workspace).build().truncated


def test_supplied_paths_rechecked(workspace, tmp_path):
    result = RepositoryMapper(workspace).build(["../outside.py", ".env.py", "pkg/auth.py"])
    assert [m.path for m in result.modules] == ["pkg/auth.py"]
    assert result.skipped == 2


def test_supplied_path_length_and_small_budget_are_bounded(workspace):
    from pydantic import ValidationError

    from codepilot.context.repository_map import ModuleEntry
    from codepilot.tools.base import ToolError

    with pytest.raises(ValidationError):
        ModuleEntry(path="x" * 513, package=False)
    with pytest.raises(ToolError, match="MAP_BUDGET_TOO_SMALL"):
        RepositoryMapper(workspace).build(max_chars=1)


def test_ast_walk_stops_at_deadline(workspace):
    from unittest.mock import patch

    with patch("codepilot.context.repository_map.time.monotonic", side_effect=[0, 0, 10]):
        result = RepositoryMapper(workspace).build(["pkg/auth.py"])
    assert result.truncated
    assert not result.modules[0].symbols
    assert result.reasons == ["deadline"]


def test_only_exact_initializer_name_marks_package(workspace):
    (workspace.root / "not__init__.py").write_text("")
    mapping = RepositoryMapper(workspace).build()
    module = next(entry for entry in mapping.modules if entry.path == "not__init__.py")
    assert not module.package


def test_empty_map_complete_but_skipped_and_syntax_failures_are_incomplete(workspace):
    from codepilot.context.repository_map import RepositoryMap

    assert not RepositoryMap().incomplete
    (workspace.root / "bad.py").write_text("def broken(:")
    result = RepositoryMapper(workspace).build(["bad.py", "missing.py"])
    assert not result.modules and result.incomplete
    assert result.skipped == result.syntax_errors == 1
    assert set(result.reasons) == {"read_error", "syntax_error"}


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("max_scan_entries", 1, "scan_limit"),
        ("max_symbols", 1, "symbol_limit"),
        ("max_map_chars", 256, "map_budget"),
        ("max_total_bytes", 1, "byte_limit"),
        ("max_file_bytes", 1, "file_size_limit"),
    ],
)
def test_map_reports_each_resource_reason(workspace, field, value, reason):
    setattr(workspace.config, field, value)
    result = RepositoryMapper(workspace).build()
    assert result.incomplete and reason in result.reasons
    assert len(result.model_dump_json()) <= workspace.config.max_map_chars


def test_repeated_failures_use_bounded_metadata(workspace):
    paths = [f"missing-{i}.py" for i in range(100)]
    result = RepositoryMapper(workspace).build(paths, max_chars=256)
    assert result.skipped == 100 and result.incomplete
    assert result.reasons == ["read_error"]
    assert len(result.model_dump_json()) <= 256


def test_input_path_limit_is_explicit(workspace):
    workspace.config.max_files = 1
    result = RepositoryMapper(workspace).build(["pkg/auth.py", "pkg/__init__.py"])
    assert result.incomplete and "input_limit" in result.reasons
