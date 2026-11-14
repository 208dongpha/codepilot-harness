import hashlib
import os

import pytest

from codepilot.config import RepositoryConfig
from codepilot.patch.guard import GuardedPath, GuardUnsupported
from codepilot.patch.host import HostPatchApplier, HostPatchError
from codepilot.tools.workspace import Workspace
from codepilot.verification.diff import GitDiffVerifier, WorkspaceSnapshot


def digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def prepared(tmp_path):
    host_root = tmp_path / "host"
    isolated_root = tmp_path / "isolated"
    host_root.mkdir()
    isolated_root.mkdir()
    (host_root / "app.py").write_text("value = 1\n", encoding="utf-8")
    (isolated_root / "app.py").write_text("value = 1\n", encoding="utf-8")
    config = RepositoryConfig(workspace=host_root, max_total_bytes=100000)
    host = Workspace(config)
    isolated = Workspace(config.model_copy(update={"workspace": isolated_root}))
    applier = HostPatchApplier(host, isolated)
    applier.begin_task()
    baseline = WorkspaceSnapshot.capture(isolated, 100000)
    (isolated_root / "app.py").write_text("value = 2\n", encoding="utf-8")
    authorized = {"app.py": digest("value = 2\n")}
    diff = GitDiffVerifier(isolated, 100000).compare(baseline, authorized)
    baseline_hashes = {
        path: digest(content) for path, content in baseline.contents.items()
    }
    plan = applier.prepare(diff, baseline_hashes, authorized)
    return host, applier, plan


def test_host_is_unchanged_until_exact_approval(tmp_path):
    host, applier, plan = prepared(tmp_path)

    assert (host.root / "app.py").read_text(encoding="utf-8") == "value = 1\n"
    with pytest.raises(HostPatchError, match="HOST_APPROVAL_REQUIRED"):
        applier.apply(plan.plan_id)
    with pytest.raises(HostPatchError, match="HOST_APPROVAL_MISMATCH"):
        applier.approve("0" * 64)
    assert (host.root / "app.py").read_text(encoding="utf-8") == "value = 1\n"

    applier.approve(plan.plan_id)
    applier.apply(plan.plan_id)
    assert (host.root / "app.py").read_text(encoding="utf-8") == "value = 2\n"


def test_external_host_change_rejects_conflict_without_writes(tmp_path):
    host, applier, plan = prepared(tmp_path)
    (host.root / "app.py").write_text("external edit\n", encoding="utf-8")
    applier.approve(plan.plan_id)

    with pytest.raises(HostPatchError, match="HOST_CONFLICT"):
        applier.apply(plan.plan_id)

    assert (host.root / "app.py").read_text(encoding="utf-8") == "external edit\n"


def prepared_multiple(tmp_path):
    host_root = tmp_path / "host"
    isolated_root = tmp_path / "isolated"
    for root in (host_root, isolated_root):
        (root / "pkg").mkdir(parents=True)
        (root / "app.py").write_text("value = 1\n", encoding="utf-8")
        (root / "pkg" / "app.py").write_text("value = 1\n", encoding="utf-8")
        (root / "unrelated.txt").write_text("unrelated\n", encoding="utf-8")
    config = RepositoryConfig(workspace=host_root, max_total_bytes=100000)
    host = Workspace(config)
    isolated = Workspace(config.model_copy(update={"workspace": isolated_root}))
    applier = HostPatchApplier(host, isolated)
    applier.begin_task()
    baseline = WorkspaceSnapshot.capture(isolated, 100000)
    changes = {"app.py": "value = 2\n", "pkg/app.py": "value = 2\n"}
    authorized = {}
    for path, content in changes.items():
        (isolated_root / path).write_text(content, encoding="utf-8")
        authorized[path] = digest(content)
    diff = GitDiffVerifier(isolated, 100000).compare(baseline, authorized)
    baseline_hashes = {
        path: digest(content) for path, content in baseline.contents.items()
    }
    plan = applier.prepare(diff, baseline_hashes, authorized)
    return host, applier, plan


def fail_second_write(applier, monkeypatch):
    original = applier._write_entry
    calls = 0

    def failing_write(entry):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise HostPatchError("HOST_APPLY_FAILED")
        return original(entry)

    monkeypatch.setattr(applier, "_write_entry", failing_write)


def test_parent_swap_after_validation_cannot_redirect_write(tmp_path, monkeypatch):
    host, applier, plan = prepared_multiple(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "app.py").write_text("outside\n", encoding="utf-8")
    original = GuardedPath.write
    swapped = False

    def swap_parent_then_write(self, content, mode):
        nonlocal swapped
        if not swapped and len(self.parts) > 1:
            swapped = True
            parent = host.root / "pkg"
            try:
                os.rename(parent, host.root / "pkg-old")
            except OSError:
                pass
            try:
                parent.symlink_to(outside, target_is_directory=True)
            except OSError:
                pass
        return original(self, content, mode)

    monkeypatch.setattr(GuardedPath, "write", swap_parent_then_write)
    applier.approve(plan.plan_id)
    try:
        applier.apply(plan.plan_id)
    except HostPatchError as exc:
        assert str(exc) in {
            "HOST_APPLY_FAILED",
            "HOST_ROLLBACK_FAILED",
            "HOST_ROLLBACK_CONFLICT",
        }

    assert (outside / "app.py").read_text(encoding="utf-8") == "outside\n"
    assert (host.root / "unrelated.txt").read_text(encoding="utf-8") == "unrelated\n"
    applied = [
        path
        for path in (host.root / "pkg" / "app.py", host.root / "pkg-old" / "app.py")
        if path.is_file()
    ]
    assert applied
    assert any(path.read_text(encoding="utf-8") == "value = 2\n" for path in applied)


def test_rollback_does_not_overwrite_concurrent_edit(tmp_path, monkeypatch):
    host, applier, plan = prepared_multiple(tmp_path)
    fail_second_write(applier, monkeypatch)
    original_rollback = applier._rollback

    def rollback_after_external_edit(plan, backups):
        (host.root / "app.py").write_text("external edit\n", encoding="utf-8")
        return original_rollback(plan, backups)

    monkeypatch.setattr(applier, "_rollback", rollback_after_external_edit)
    applier.approve(plan.plan_id)

    with pytest.raises(HostPatchError, match="HOST_ROLLBACK_CONFLICT"):
        applier.apply(plan.plan_id)

    assert (host.root / "app.py").read_text(encoding="utf-8") == "external edit\n"
    assert (host.root / "pkg" / "app.py").read_text(encoding="utf-8") == "value = 1\n"
    assert (host.root / "unrelated.txt").read_text(encoding="utf-8") == "unrelated\n"


def test_rollback_parent_swap_cannot_escape(tmp_path, monkeypatch):
    host, applier, plan = prepared_multiple(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "app.py").write_text("outside\n", encoding="utf-8")
    fail_second_write(applier, monkeypatch)
    original_rollback = applier._rollback

    def rollback_after_parent_swap(plan, backups):
        parent = host.root / "pkg"
        try:
            os.rename(parent, host.root / "pkg-old")
        except OSError:
            pass
        try:
            parent.symlink_to(outside, target_is_directory=True)
        except OSError:
            pass
        return original_rollback(plan, backups)

    monkeypatch.setattr(applier, "_rollback", rollback_after_parent_swap)
    applier.approve(plan.plan_id)

    with pytest.raises(HostPatchError) as caught:
        applier.apply(plan.plan_id)
    assert str(caught.value) in {
        "HOST_APPLY_FAILED",
        "HOST_ROLLBACK_FAILED",
        "HOST_ROLLBACK_CONFLICT",
    }
    assert (outside / "app.py").read_text(encoding="utf-8") == "outside\n"
    assert (host.root / "unrelated.txt").read_text(encoding="utf-8") == "unrelated\n"


def test_unsupported_platform_fails_before_host_mutation(tmp_path, monkeypatch):
    host, applier, plan = prepared_multiple(tmp_path)

    def unsupported(self):
        raise GuardUnsupported("GUARD_UNSUPPORTED")

    monkeypatch.setattr(GuardedPath, "__enter__", unsupported)
    applier.approve(plan.plan_id)

    with pytest.raises(HostPatchError, match="HOST_UNSUPPORTED"):
        applier.apply(plan.plan_id)

    assert (host.root / "app.py").read_text(encoding="utf-8") == "value = 1\n"
    assert (host.root / "pkg" / "app.py").read_text(encoding="utf-8") == "value = 1\n"


def test_host_symlink_swap_is_rejected(tmp_path):
    host, applier, plan = prepared(tmp_path)
    outside = tmp_path / "outside.py"
    outside.write_text("outside\n", encoding="utf-8")
    target = host.root / "app.py"
    target.unlink()
    try:
        target.symlink_to(outside)
    except OSError:
        pytest.skip("Symlink creation is unavailable.")
    applier.approve(plan.plan_id)

    with pytest.raises(HostPatchError, match="HOST_CONFLICT"):
        applier.apply(plan.plan_id)

    assert outside.read_text(encoding="utf-8") == "outside\n"
    assert target.is_symlink()


def test_rollback_parent_swap_does_not_remove_outside_directory(tmp_path, monkeypatch):
    host_root = tmp_path / "host"
    isolated_root = tmp_path / "isolated"
    host_root.mkdir()
    isolated_root.mkdir()
    (host_root / "app.py").write_text("value = 1\n", encoding="utf-8")
    (isolated_root / "app.py").write_text("value = 1\n", encoding="utf-8")
    (host_root / "unrelated.txt").write_text("unrelated\n", encoding="utf-8")
    config = RepositoryConfig(workspace=host_root, max_total_bytes=100000)
    host = Workspace(config)
    isolated = Workspace(config.model_copy(update={"workspace": isolated_root}))
    applier = HostPatchApplier(host, isolated)
    applier.begin_task()
    baseline = WorkspaceSnapshot.capture(isolated, 100000)
    (isolated_root / "aaa" / "sub").mkdir(parents=True)
    (isolated_root / "aaa" / "sub" / "new.py").write_text("created\n", encoding="utf-8")
    (isolated_root / "app.py").write_text("value = 2\n", encoding="utf-8")
    authorized = {
        "aaa/sub/new.py": digest("created\n"),
        "app.py": digest("value = 2\n"),
    }
    diff = GitDiffVerifier(isolated, 100000).compare(baseline, authorized)
    baseline_hashes = {
        path: digest(content) for path, content in baseline.contents.items()
    }
    plan = applier.prepare(diff, baseline_hashes, authorized)
    fail_second_write(applier, monkeypatch)
    outside = tmp_path / "outside"
    (outside / "sub").mkdir(parents=True)
    swapped = False
    original_rollback = applier._rollback

    def rollback_after_swap(plan, backups):
        nonlocal swapped
        try:
            os.rename(host_root / "aaa", host_root / "aaa-old")
            (host_root / "aaa").symlink_to(outside, target_is_directory=True)
            swapped = True
        except OSError:
            pass
        return original_rollback(plan, backups)

    monkeypatch.setattr(applier, "_rollback", rollback_after_swap)
    applier.approve(plan.plan_id)

    with pytest.raises(HostPatchError):
        applier.apply(plan.plan_id)

    assert (outside / "sub").is_dir()
    assert list(outside.iterdir()) == [outside / "sub"]
    assert (host.root / "app.py").read_text(encoding="utf-8") == "value = 1\n"
    assert (host.root / "unrelated.txt").read_text(encoding="utf-8") == "unrelated\n"
    if not swapped and os.name == "nt":
        pytest.skip("Symlink creation is unavailable on this Windows host.")
