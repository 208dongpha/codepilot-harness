import hashlib
import os
import stat
import tempfile
from pathlib import Path
from threading import Event, Thread

import pytest

from codepilot.config import RepositoryConfig
from codepilot.patch.tool import IsolatedWorkspace, PatchError, PatchTool
from codepilot.permissions.engine import PermissionEngine, PermissionError
from codepilot.tools.base import ToolError
from codepilot.tools.workspace import FileListing, Workspace


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


def test_create_file_in_new_nested_directories(isolated):
    host, sandbox = isolated
    engine = PermissionEngine()
    tool = PatchTool(sandbox, engine)
    proposal = tool.propose_create("new/package/module.py", "created\n")
    approve(engine, proposal)

    tool.apply(proposal)

    assert (sandbox.root / "new/package/module.py").read_text(encoding="utf-8") == "created\n"
    assert not (host / "new").exists()


def test_nested_create_rejects_symlink_added_after_proposal(isolated, tmp_path):
    _, sandbox = isolated
    outside = tmp_path / "outside"
    outside.mkdir()
    engine = PermissionEngine()
    tool = PatchTool(sandbox, engine)
    proposal = tool.propose_create("new/module.py", "created\n")
    try:
        (sandbox.root / "new").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlink creation is unavailable.")
    approve(engine, proposal)

    with pytest.raises(PatchError, match="UNSAFE_TARGET"):
        tool.apply(proposal)
    assert not (outside / "module.py").exists()


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


def test_mutating_returned_pending_action_cannot_change_approved_content(isolated):
    _, sandbox = isolated
    engine = PermissionEngine()
    tool = PatchTool(sandbox, engine)
    original = tool.propose_replace("app.py", "approved\n")
    exposed = engine.request(original.action)
    engine.approve(original.action.action_id)

    tampered = original.model_copy(update={"content": "tampered\n"})
    exposed.action.arguments["content_sha256"] = tampered.action.arguments["content_sha256"]

    with pytest.raises(PermissionError, match="APPROVAL_MISMATCH"):
        tool.apply(tampered)
    assert (sandbox.root / "app.py").read_text(encoding="utf-8") == "old\n"


def test_mutation_after_approval_consumption_cannot_change_effective_patch(isolated, monkeypatch):
    _, sandbox = isolated
    engine = PermissionEngine()
    tool = PatchTool(sandbox, engine)
    proposal = tool.propose_replace("app.py", "approved\n")
    approve(engine, proposal)
    original_consume = engine.consume

    def mutate_after_consume(action):
        consumed = original_consume(action)
        object.__setattr__(proposal, "content", "tampered\n")
        return consumed

    monkeypatch.setattr(engine, "consume", mutate_after_consume)
    tool.apply(proposal)

    assert (sandbox.root / "app.py").read_text(encoding="utf-8") == "approved\n"


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


def test_copy_omits_non_text_and_sensitive_sources_without_reading_them(tmp_path, monkeypatch):
    host = tmp_path / "host"
    host.mkdir()
    (host / "app.py").write_text("value = 1\n", encoding="utf-8")
    (host / ".env").write_text("GEMINI_API_KEY=synthetic\n", encoding="utf-8")
    (host / "binary.py").write_bytes(b"\x00\x01")
    (host / "leak.py").write_text(
        'password = "synthetic-private-value"\n', encoding="utf-8"
    )
    (host / "large.py").write_bytes(b"x" * (RepositoryConfig().max_file_bytes + 1))
    workspace = Workspace(RepositoryConfig(workspace=host))
    read: list[str] = []
    real_read = workspace.read_text

    def recording_read(relative):
        read.append(relative)
        return real_read(relative)

    monkeypatch.setattr(workspace, "read_text", recording_read)

    isolated = IsolatedWorkspace.create(workspace)
    try:
        assert isolated.copied == 1
        assert isolated.omitted == 3
        assert read == ["app.py", "binary.py", "large.py", "leak.py"]
        assert (isolated.root / "app.py").read_text(encoding="utf-8") == "value = 1\n"
        assert not (isolated.root / ".env").exists()
        assert not (isolated.root / "binary.py").exists()
        assert not (isolated.root / "secret.py").exists()
    finally:
        isolated.close()


def test_copy_fails_closed_when_a_listed_file_cannot_be_read(tmp_path, monkeypatch):
    host = tmp_path / "host"
    host.mkdir()
    (host / "app.py").write_text("value = 1\n", encoding="utf-8")
    workspace = Workspace(RepositoryConfig(workspace=host))
    monkeypatch.setattr(
        workspace,
        "read_text",
        lambda relative: (_ for _ in ()).throw(ToolError("PERMISSION_DENIED")),
    )
    pattern = "codepilot-isolated-*"
    before = set(Path(tempfile.gettempdir()).glob(pattern))

    with pytest.raises(PatchError, match="ISOLATION_INCOMPLETE"):
        IsolatedWorkspace.create(workspace)

    assert set(Path(tempfile.gettempdir()).glob(pattern)) == before


@pytest.mark.parametrize("truncated,skipped", [(True, 0), (False, 1)])
def test_copy_fails_closed_on_incomplete_listing(tmp_path, monkeypatch, truncated, skipped):
    host = tmp_path / "host"
    host.mkdir()
    (host / "app.py").write_text("value = 1\n", encoding="utf-8")
    workspace = Workspace(RepositoryConfig(workspace=host))
    monkeypatch.setattr(
        workspace,
        "list_files",
        lambda *args, **kwargs: FileListing(
            ["app.py"], truncated=truncated, skipped=skipped
        ),
    )

    with pytest.raises(PatchError, match="ISOLATION_INCOMPLETE"):
        IsolatedWorkspace.create(workspace)


def test_copy_rejects_a_listing_path_that_escapes(tmp_path, monkeypatch):
    host = tmp_path / "host"
    host.mkdir()
    (tmp_path / "outside.py").write_text("outside\n", encoding="utf-8")
    workspace = Workspace(RepositoryConfig(workspace=host))
    monkeypatch.setattr(
        workspace,
        "list_files",
        lambda *args, **kwargs: FileListing(["../outside.py"]),
    )
    monkeypatch.setattr(workspace, "read_text", lambda relative: "outside\n")

    with pytest.raises(PatchError, match="UNSAFE_TARGET"):
        IsolatedWorkspace.create(workspace)


def test_copy_rejects_total_size_over_limit(tmp_path):
    host = tmp_path / "host"
    host.mkdir()
    (host / "app.py").write_text("value = 1\n", encoding="utf-8")
    workspace = Workspace(RepositoryConfig(workspace=host, max_total_bytes=1))

    with pytest.raises(PatchError, match="ISOLATION_TOO_LARGE"):
        IsolatedWorkspace.create(workspace)
    assert (host / "app.py").read_text(encoding="utf-8") == "value = 1\n"


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits are not enforced on Windows.")
def test_copy_preserves_executable_bit_without_special_bits(tmp_path):
    host = tmp_path / "host"
    host.mkdir()
    script = host / "run.sh"
    script.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    script.chmod(0o4755)
    workspace = Workspace(RepositoryConfig(workspace=host))

    isolated = IsolatedWorkspace.create(workspace)
    try:
        mode = stat.S_IMODE((isolated.root / "run.sh").stat().st_mode)
        assert mode == 0o755
        assert not mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX)
        assert stat.S_IMODE(script.stat().st_mode) == 0o4755
    finally:
        isolated.close()

