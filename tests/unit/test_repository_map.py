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
