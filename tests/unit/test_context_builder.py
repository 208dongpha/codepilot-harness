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


@pytest.mark.parametrize("budget", [900, 1200])
@pytest.mark.parametrize("unicode", [False, True])
def test_budget_keeps_later_focus_and_exact_line_slice(workspace, budget, unicode):
    lines = [f"# {'nÃ¡Â»â„¢i dung' if unicode else 'content'} {i} " + "x" * 60 for i in range(70)]
    lines[45] = "# focus-marker"
    (workspace.root / "later.txt").write_text("\n".join(lines), encoding="utf-8")
    workspace.config.max_context_chars = budget
    result = ContextBuilder(workspace).build(
        "Explain later.txt", RepositoryMap(), [{"path": "later.txt", "line": 46}]
    )
    assert result.sources
    source = result.sources[0]
    assert source.start_line <= 46 <= source.end_line
    assert source.content == "\n".join(lines[source.start_line - 1 : source.end_line])
    assert not source.partial_line
    assert len(result.to_message()["content"]) <= budget


def test_single_oversized_unicode_line_is_explicitly_partial(workspace):
    line = "TiÃ¡ÂºÂ¿ng ViÃ¡Â»â€¡t Ã°Å¸Ëœâ‚¬ " * 500
    (workspace.root / "long.txt").write_text(line, encoding="utf-8")
    workspace.config.max_context_chars = 1000
    result = ContextBuilder(workspace).build("long.txt", RepositoryMap())
    assert result.sources
    source = result.sources[0]
    assert source.partial_line and source.truncated
    assert source.start_line == source.end_line == 1
    assert source.content and line.startswith(source.content)
    assert len(result.to_message()["content"]) <= 1000


def test_empty_complete_context_is_distinct_from_empty_incomplete(workspace):
    complete = ContextBuilder(workspace).build("zebra", RepositoryMapper(workspace).build())
    assert not complete.sources and not complete.incomplete
    prior = RepositoryMap(skipped=2, syntax_errors=1, truncated=True)
    result = ContextBuilder(workspace).build("zebra", prior)
    assert not result.sources and result.incomplete
    assert result.input_map_skipped == 2 and result.input_map_syntax_errors == 1
    assert "input_incomplete" in result.reasons


@pytest.mark.parametrize(
    "limit,reason",
    [
        ("max_scan_entries", "scan_limit"),
        ("max_total_bytes", "byte_limit"),
        ("max_context_files", "source_limit"),
    ],
)
def test_context_reports_resource_omissions(workspace, limit, reason):
    mapping = RepositoryMapper(workspace).build()
    setattr(workspace.config, limit, 1)
    result = ContextBuilder(workspace).build("auth other README", mapping)
    assert result.incomplete and reason in result.reasons
    assert len(result.to_message()["content"]) <= workspace.config.max_context_chars


def test_unreadable_relevant_source_has_safe_diagnostics(workspace):
    from unittest.mock import patch

    with patch.object(workspace, "read_text", side_effect=ToolError("PERMISSION_DENIED")):
        result = ContextBuilder(workspace).build("README", RepositoryMap())
    assert not result.sources and result.incomplete
    assert result.skipped_sources == result.omitted_sources == 1
    assert result.reasons == ["read_error"]


def test_budget_omission_and_inherited_map_diagnostics_are_retained(workspace):
    (workspace.root / "auth.py").write_text("def authenticate():\n" + "    pass\n" * 100)
    workspace.config.max_map_chars = 256
    mapping = RepositoryMapper(workspace).build()
    workspace.config.max_context_chars = 1100
    result = ContextBuilder(workspace).build("auth other", mapping)
    assert result.incomplete
    assert "map_budget" in result.reasons
    assert "context_budget" in result.reasons
    assert len(result.to_message()["content"]) <= 1100


def test_empty_relevant_file_is_not_a_failed_exploration(workspace):
    (workspace.root / "empty.txt").write_text("")
    result = ContextBuilder(workspace).build("empty.txt", RepositoryMap())
    assert not result.sources and not result.incomplete and result.omitted_sources == 0


def test_unreadable_scan_entry_is_not_reported_as_complete(workspace):
    from unittest.mock import patch

    original = workspace.resolve

    def resolve(path, **kwargs):
        if path == "README.md":
            raise ToolError("PERMISSION_DENIED")
        return original(path, **kwargs)

    with patch.object(workspace, "resolve", side_effect=resolve):
        result = ContextBuilder(workspace).build("README", RepositoryMap())
    assert not result.sources and result.incomplete and result.scan_skipped == 1
    assert "read_error" in result.reasons


def test_ranking_deadline_is_explicit(workspace):
    from unittest.mock import patch

    from codepilot.tools.workspace import FileListing

    with (
        patch("codepilot.context.builder.RepositoryMapper.build", return_value=RepositoryMap()),
        patch.object(workspace, "list_files", return_value=FileListing(["auth.py"])),
        patch("codepilot.context.builder.time.monotonic", side_effect=[0, 10]),
    ):
        result = ContextBuilder(workspace).build("auth", RepositoryMap())
    assert not result.sources and result.incomplete
    assert result.reasons == ["deadline"]


def test_metadata_cannot_silently_overflow_small_budget(workspace):
    workspace.config.max_context_chars = 512
    prior = RepositoryMap(
        skipped=100,
        syntax_errors=100,
        truncated=True,
        reasons=["scan_limit", "read_error", "map_budget"],
    )
    with pytest.raises(ToolError, match="CONTEXT_BUDGET_EXCEEDED"):
        ContextBuilder(workspace).build("Explain auth", prior)
