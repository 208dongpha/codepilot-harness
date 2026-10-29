import shutil

import pytest

from codepilot.config import RepositoryConfig
from codepilot.tools.factory import create_read_only_registry


@pytest.fixture
def repo(tmp_path):
    if not shutil.which("rg"):
        pytest.skip("ripgrep is not installed; real search NOT VERIFIED")
    root = tmp_path / "repo"
    root.mkdir()
    (root / "main.py").write_text("def login():\n    return 'a.b'\n", encoding="utf-8")
    (root / ".env").write_text("fixture-hidden")
    (root / "config.txt").write_text('password="fixture-hidden-value"')
    (root / "binary.bin").write_bytes(b"login\x00")
    (root / "bad.txt").write_bytes(b"login\xff")
    (root / "large.txt").write_text("login" * 1000)
    settings = RepositoryConfig(workspace=root, max_file_bytes=1024)
    return root, create_read_only_registry(settings)


def test_real_literal_regex_invalid_and_empty(repo):
    root, tools = repo
    result = tools.call("search_code", {"query": "login"})
    assert result.ok
    assert result.data["matches"] == [{"path": "main.py", "line": 1, "content": "def login():"}]
    assert result.data["skipped"] == 4
    assert tools.call("search_code", {"query": "a.b"}).data["matches"][0]["line"] == 2
    assert tools.call("search_code", {"query": r"def\s+login", "regex": True}).data["matches"]
    assert tools.call("search_code", {"query": "[", "regex": True}).error == "INVALID_REGEX"
    assert tools.call("search_code", {"query": "not-present"}).data["matches"] == []
    assert tools.call("search_code", {"query": "fixture-hidden"}).data["matches"] == []
    assert tools.call("search_code", {"query": "login", "path": "../"}).error == "ACCESS_DENIED"


def test_real_search_output_and_result_limits(repo):
    root, tools = repo
    (root / "long.py").write_text("login " * 150)
    result = tools.call("search_code", {"query": "login", "limit": 1})
    assert result.ok and len(result.data["matches"]) == 1
    assert result.data["truncated"]
    assert len(result.model_dump_json()) <= 32000


def test_rg_never_follows_repository_symlink(repo, tmp_path):
    root, tools = repo
    outside = tmp_path / "outside.txt"
    outside.write_text("outside-marker")
    try:
        (root / "alias.txt").symlink_to(outside)
    except OSError:
        pytest.skip("Symlink creation not permitted")
    assert tools.call("search_code", {"query": "outside-marker"}).data["matches"] == []
