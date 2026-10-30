import hashlib
from threading import Event, Thread

import pytest

from codepilot.config import RepositoryConfig
from codepilot.patch.tool import IsolatedWorkspace, PatchError, PatchTool
from codepilot.permissions.engine import PermissionEngine, PermissionError
from codepilot.tools.workspace import Workspace


@pytest.fixture
def isolated(tmp_path):
    host = tmp_path / "host"
    host.mkdir()
    (host / "app.py").write_text("old\n", encoding="utf-8")
    (host / ".env").write_text("GEMINI_API_KEY=synthetic\n", encoding="utf-8")
    sandbox = IsolatedWorkspace.create(Workspace(RepositoryConfig(workspace=host)))
    try:
        yield host, sandbox
    finally:
        sandbox.close()


def approve(engine, proposal):
    engine.request(proposal.action)
    engine.approve(proposal.action.action_id)


def test_approved_patch_changes_only_isolated_workspace(isolated):
    host, sandbox = isolated
    engine = PermissionEngine()
    tool = PatchTool(sandbox, engine)
    proposal = tool.propose_replace("app.py", "new\n")
    approve(engine, proposal)

    result = tool.apply(proposal)

    assert result.sha256 == hashlib.sha256(b"new\n").hexdigest()
    assert (sandbox.root / "app.py").read_text(encoding="utf-8") == "new\n"
    assert (host / "app.py").read_text(encoding="utf-8") == "old\n"
    assert not (sandbox.root / ".env").exists()


def test_create_and_delete_are_conflict_checked_and_approval_scoped(isolated):
    host, sandbox = isolated
    engine = PermissionEngine()
    tool = PatchTool(sandbox, engine)

    create = tool.propose_create("new.py", "created\n")
    approve(engine, create)
    tool.apply(create)
    assert (sandbox.root / "new.py").read_text(encoding="utf-8") == "created\n"
    assert not (host / "new.py").exists()
    with pytest.raises(PatchError, match="PATCH_CONFLICT"):
        tool.propose_create("new.py", "again")

    delete = tool.propose_delete("new.py")
    approve(engine, delete)
    tool.apply(delete)
    assert not (sandbox.root / "new.py").exists()
    assert (host / "app.py").read_text(encoding="utf-8") == "old\n"


def test_denial_replay_and_unauthorized_write_have_no_side_effect(isolated):
    _, sandbox = isolated
    engine = PermissionEngine()
    tool = PatchTool(sandbox, engine)
    proposal = tool.propose_replace("app.py", "new\n")
    engine.request(proposal.action)
    engine.deny(proposal.action.action_id)
    with pytest.raises(PermissionError, match="APPROVAL_DENIED"):
        tool.apply(proposal)
    assert (sandbox.root / "app.py").read_text(encoding="utf-8") == "old\n"

    approve(engine, proposal)
    tool.apply(proposal)
    with pytest.raises(PermissionError, match="APPROVAL_ALREADY_USED"):
        tool.apply(proposal)


@pytest.mark.parametrize("path", ["../outside.py", "/outside.py", "secrets/key.py"])
def test_unsafe_patch_targets_are_rejected(isolated, path):
    _, sandbox = isolated
    with pytest.raises(PatchError, match="UNSAFE_TARGET"):
        PatchTool(sandbox, PermissionEngine()).propose_replace(path, "content")


def test_symlink_escape_and_sensitive_content_are_rejected(isolated, tmp_path):
    _, sandbox = isolated
    outside = tmp_path / "outside.py"
    outside.write_text("outside", encoding="utf-8")
    try:
        (sandbox.root / "link.py").symlink_to(outside)
    except OSError:
        pytest.skip("Symlink creation is unavailable.")
    tool = PatchTool(sandbox, PermissionEngine())
    with pytest.raises(PatchError, match="UNSAFE_TARGET"):
        tool.propose_replace("link.py", "new")
    with pytest.raises(PatchError, match="SENSITIVE_CONTENT"):
        tool.propose_replace("app.py", 'api_key = "synthetic-private-value"')


def test_stale_patch_and_conflicting_approval_are_rejected(isolated):
    _, sandbox = isolated
    engine = PermissionEngine()
    tool = PatchTool(sandbox, engine)
    proposal = tool.propose_replace("app.py", "new\n")
    approve(engine, proposal)
    (sandbox.root / "app.py").write_text("concurrent\n", encoding="utf-8")
    with pytest.raises(PatchError, match="STALE_PATCH"):
        tool.apply(proposal)
    assert (sandbox.root / "app.py").read_text(encoding="utf-8") == "concurrent\n"

    different = tool.propose_replace("app.py", "other\n")
    with pytest.raises(PermissionError, match="APPROVAL_MISMATCH"):
        tool.apply(different)


def test_concurrent_apply_attempt_is_rejected(isolated, monkeypatch):
    _, sandbox = isolated
    engine = PermissionEngine()
    tool = PatchTool(sandbox, engine)
    proposal = tool.propose_replace("app.py", "new\n")
    approve(engine, proposal)
    entered = Event()
    release = Event()
    original = tool._write_atomic

    def slow_write(*args, **kwargs):
        entered.set()
        release.wait(2)
        return original(*args, **kwargs)

    monkeypatch.setattr(tool, "_write_atomic", slow_write)
    thread = Thread(target=tool.apply, args=(proposal,))
    thread.start()
    assert entered.wait(2)
    with pytest.raises(PatchError, match="PATCH_BUSY"):
        tool.apply(proposal)
    release.set()
    thread.join(2)
    assert not thread.is_alive()
