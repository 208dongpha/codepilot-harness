import subprocess
from unittest.mock import patch

import pytest

from codepilot.config import RepositoryConfig
from codepilot.tools.factory import create_read_only_registry
from codepilot.tools.search import SearchArgs, SearchTool
from codepilot.tools.workspace import Workspace


@pytest.fixture
def tools(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "module.py").write_text("def login():\n    return 'a.b'\n", encoding="utf-8")
    return create_read_only_registry(RepositoryConfig(workspace=root))


def test_missing_rg_and_no_fallback(tools):
    with patch("shutil.which", return_value=None), patch("subprocess.run") as run:
        result = tools.call("search_code", {"query": "login"})
        assert result.error == "RIPGREP_UNAVAILABLE"
        run.assert_not_called()


def test_rg_timeout(tools):
    with (
        patch("shutil.which", return_value="C:/trusted/rg.exe"),
        patch("subprocess.run", side_effect=subprocess.TimeoutExpired("rg", 1)) as run,
    ):
        assert tools.call("search_code", {"query": "login"}).error == "SEARCH_TIMEOUT"
        assert run.call_args.kwargs["timeout"] > 0
        assert not run.call_args.kwargs.get("shell", False)


def test_rg_arguments_and_environment_are_not_repository_controlled(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.py").write_text("value")
    workspace = Workspace(RepositoryConfig(workspace=root))
    search = SearchTool(workspace)
    with patch("subprocess.run", return_value=subprocess.CompletedProcess([], 1, "", "")) as run:
        search._run("C:/trusted/rg.exe", "value", SearchArgs(query="--pre=evil"), 10, 1)
    command = run.call_args.args[0]
    assert command[-2:] == ["--", "-"]
    assert command[command.index("--regexp") + 1] == "--pre=evil"
    assert "--no-config" in command and "--engine=default" in command
    assert "GEMINI_API_KEY" not in run.call_args.kwargs["env"]


def test_untrusted_rg_is_not_executed(tmp_path):
    (tmp_path / "rg.exe").write_text("untrusted")
    tools = create_read_only_registry(RepositoryConfig(workspace=tmp_path))
    with (
        patch("shutil.which", return_value=str(tmp_path / "rg.exe")),
        patch("subprocess.run") as run,
    ):
        assert tools.call("search_code", {"query": "x"}).error == "UNTRUSTED_RIPGREP"
        run.assert_not_called()
