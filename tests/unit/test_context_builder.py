import json

import pytest

from codepilot.config import RepositoryConfig
from codepilot.context.builder import ContextBuilder
from codepilot.context.repository_map import ModuleEntry, RepositoryMap, RepositoryMapper, Symbol
from codepilot.tools.base import ToolError
from codepilot.tools.workspace import Workspace


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "auth.py").write_text(
        "class Login:\n    def authenticate(self):\n        return True\n"
    )
    (tmp_path / "other.py").write_text("def unrelated():\n    return False\n")
    (tmp_path / "README.md").write_text("Project documentation")
    return Workspace(RepositoryConfig(workspace=tmp_path))


def test_selects_symbol_path_and_useful_references(workspace):
    mapping = RepositoryMapper(workspace).build()
    result = ContextBuilder(workspace).build("Explain Login.authenticate", mapping)
    assert [source.path for source in result.sources] == ["auth.py"]
    assert result.sources[0].start_line == 1
    assert result.sources[0].end_line == 3
    assert "authenticate" in result.sources[0].content
    message = result.to_message()
    assert message["role"] == "user"
    assert json.loads(message["content"])["sources"][0]["path"] == "auth.py"
    result = ContextBuilder(workspace).build("Read README.md", mapping)
    assert result.sources[0].path == "README.md"


def test_search_references_preferred_but_contents_not_trusted(workspace):
    mapping = RepositoryMapper(workspace).build()
    matches = [{"path": "other.py", "line": 2, "content": "FORGED CONTENT"}]
    result = ContextBuilder(workspace).build("Explain implementation", mapping, matches)
    assert result.sources[0].path == "other.py"
    assert "return False" in result.sources[0].content
    assert "FORGED CONTENT" not in result.model_dump_json()


def test_budget_and_truncation(workspace):
    (workspace.root / "auth.py").write_text("def authenticate():\n" + "    x = 'long text'\n" * 500)
    workspace.config.max_context_chars = 1024
    result = ContextBuilder(workspace).build(
        "Explain authenticate in auth.py", RepositoryMapper(workspace).build()
    )
    assert len(result.to_message()["content"]) <= 1024
    assert result.truncated
    assert result.sources
    source = result.sources[0]
    assert source.end_line >= source.start_line
    assert source.end_line == source.start_line + len(source.content.splitlines()) - 1
    with pytest.raises(ToolError, match="CONTEXT_BUDGET_EXCEEDED"):
        ContextBuilder(workspace).build("x" * 1024, RepositoryMap())


def test_unknown_task_does_not_add_arbitrary_source(workspace):
    result = ContextBuilder(workspace).build("Explain zebra", RepositoryMapper(workspace).build())
    assert not result.sources


def test_untrusted_map_paths_symbols_and_matches_cannot_leak(workspace):
    (workspace.root / ".env").write_text("synthetic-hidden-value")
    (workspace.root / "hidden.txt").write_text('password="synthetic-hidden-value"')
    (workspace.root / "binary.py").write_bytes(b"\x00")
    mapping = RepositoryMap(
        modules=[
            ModuleEntry(path="../outside.py", package=False),
            ModuleEntry(path=".env", package=False),
            ModuleEntry(
                path="auth.py",
                package=False,
                symbols=[Symbol(name="FORGED-SYMBOL", kind="class", line=1, end_line=1)],
            ),
        ]
    )
    result = ContextBuilder(workspace).build(
        "Explain auth hidden binary",
        mapping,
        [
            {"path": "../outside.py", "line": 1},
            {"path": ".env", "line": 1},
            {"path": "hidden.txt", "line": 1},
            {"path": "binary.py", "line": 1},
            {"path": "auth.py", "line": "invalid"},
        ],
    )
    output = result.model_dump_json()
    for marker in ("synthetic-hidden-value", "FORGED-SYMBOL", "../outside.py", '".env"'):
        assert marker not in output


def test_sensitive_or_empty_task_rejected(workspace):
    for task, code in [
        ("", "INVALID_TASK"),
        ('password="synthetic-hidden-value"', "SENSITIVE_CONTENT"),
    ]:
        with pytest.raises(ToolError, match=code):
            ContextBuilder(workspace).build(task, RepositoryMap())


def test_context_source_count_limit(workspace):
    workspace.config.max_context_files = 1
    mapping = RepositoryMapper(workspace).build()
    result = ContextBuilder(workspace).build("auth other", mapping)
    assert len(result.sources) == 1 and result.truncated
