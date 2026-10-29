import shutil

import pytest

from codepilot.config import RepositoryConfig
from codepilot.context.builder import ContextBuilder
from codepilot.context.repository_map import RepositoryMapper
from codepilot.tools.factory import create_read_only_registry
from codepilot.tools.workspace import Workspace


def test_complete_read_only_flow_does_not_execute_or_modify_target(tmp_path):
    if not shutil.which("rg"):
        pytest.skip("ripgrep is unavailable; end-to-end search NOT VERIFIED")
    root = tmp_path / "repo"
    root.mkdir()
    source = root / "logic.py"
    source.write_text("raise RuntimeError('must not execute')\ndef calculate():\n    return 42\n")
    (root / ".env").write_text("synthetic-private-value")
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    settings = RepositoryConfig(workspace=root, max_context_chars=2048)
    registry = create_read_only_registry(settings)
    assert registry.names == ("list_files", "read_file", "search_code")
    assert registry.call("list_files", {}).data["paths"] == ["logic.py"]
    assert registry.call("read_file", {"path": "logic.py"}).ok
    matches = registry.call("search_code", {"query": "calculate"})
    assert matches.ok
    workspace = Workspace(settings)
    mapping = RepositoryMapper(workspace).build()
    context = ContextBuilder(workspace).build("Explain calculate", mapping, matches.data["matches"])
    assert context.sources[0].path == "logic.py"
    assert len(context.to_message()["content"]) <= 2048
    assert "synthetic-private-value" not in context.model_dump_json()
    assert before == {p.name: p.read_bytes() for p in root.iterdir()}
