import hashlib
from pathlib import Path

import pytest

from codepilot.config import RepositoryConfig
from codepilot.patch.host import HostPatchApplier, HostPatchError
from codepilot.tools.workspace import FileListing, Workspace
from codepilot.verification.diff import GitDiffVerifier, WorkspaceSnapshot


def digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def copy_tree(source: Path, target: Path) -> None:
    for path in source.rglob("*"):
        if path.is_file():
            destination = target / path.relative_to(source)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")


@pytest.fixture
def env(tmp_path):
    host_root = tmp_path / "host"
    isolated_root = tmp_path / "isolated"
    host_root.mkdir()
    isolated_root.mkdir()
    (host_root / "app.py").write_text("value = 1\n", encoding="utf-8")
    (host_root / "keep.txt").write_text("keep\n", encoding="utf-8")
    copy_tree(host_root, isolated_root)
    config = RepositoryConfig(workspace=host_root, max_total_bytes=100000)
    host = Workspace(config)
    isolated = Workspace(config.model_copy(update={"workspace": isolated_root}))
    applier = HostPatchApplier(host, isolated)
    applier.begin_task()
    return host, isolated, applier


def plan_for(env, changes):
    host, isolated, applier = env
    baseline = WorkspaceSnapshot.capture(isolated, 100000)
    authorized = {}
    for path, content in changes.items():
        target = isolated.root / path
        if content is None:
            target.unlink()
            authorized[path] = None
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            authorized[path] = digest(content)
    diff = GitDiffVerifier(isolated, 100000).compare(baseline, authorized)
    baseline_hashes = {
        path: digest(content) for path, content in baseline.contents.items()
    }
    plan = applier.prepare(diff, baseline_hashes, authorized)
    return diff, plan


def test_prepare_is_side_effect_free_and_apply_is_exact_single_use(env):
    host, _, applier = env
    _, plan = plan_for(env, {"app.py": "value = 2\n"})

    assert plan is not None
    assert (host.root / "app.py").read_text(encoding="utf-8") == "value = 1\n"
    with pytest.raises(HostPatchError, match="HOST_APPROVAL_MISMATCH"):
        applier.approve("0" * 64)
    with pytest.raises(HostPatchError, match="HOST_APPROVAL_REQUIRED"):
        applier.apply(plan.plan_id)
    applier.approve(plan.plan_id)
    applied = applier.apply(plan.plan_id)

    assert applied.files == ("app.py",)
    assert (host.root / "app.py").read_text(encoding="utf-8") == "value = 2\n"
    with pytest.raises(HostPatchError, match="HOST_APPROVAL_MISMATCH"):
        applier.apply(plan.plan_id)


def test_host_change_after_preparation_is_rejected_without_writes(env):
    host, _, applier = env
    _, plan = plan_for(env, {"app.py": "value = 2\n"})
    (host.root / "app.py").write_text("user change\n", encoding="utf-8")
    applier.approve(plan.plan_id)

    with pytest.raises(HostPatchError, match="HOST_CONFLICT"):
        applier.apply(plan.plan_id)

    assert (host.root / "app.py").read_text(encoding="utf-8") == "user change\n"
    assert not list(host.root.glob(".codepilot-apply-*"))


def test_multi_file_failure_rolls_back_every_change(env, monkeypatch):
    host, _, applier = env
    _, plan = plan_for(
        env, {"app.py": "value = 2\n", "pkg/new.py": "created\n", "keep.txt": None}
    )
    applier.approve(plan.plan_id)
    original = applier._write_entry
    calls = 0

    def failing_write(entry):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise HostPatchError("HOST_APPLY_FAILED")
        return original(entry)

    monkeypatch.setattr(applier, "_write_entry", failing_write)

    with pytest.raises(HostPatchError, match="HOST_APPLY_FAILED"):
        applier.apply(plan.plan_id)

    assert (host.root / "app.py").read_text(encoding="utf-8") == "value = 1\n"
    assert (host.root / "keep.txt").read_text(encoding="utf-8") == "keep\n"
    assert not (host.root / "pkg").exists()
    assert not list(host.root.rglob(".codepilot-apply-*"))


def test_added_nested_file_is_created_and_deleted_file_is_removed(env):
    host, _, applier = env
    _, plan = plan_for(env, {"pkg/new.py": "created\n", "keep.txt": None})
    applier.approve(plan.plan_id)
    applied = applier.apply(plan.plan_id)

    assert set(applied.files) == {"pkg/new.py", "keep.txt"}
    assert (host.root / "pkg/new.py").read_text(encoding="utf-8") == "created\n"
    assert not (host.root / "keep.txt").exists()


def test_prepare_rejects_unverified_or_binary_diffs(env):
    from codepilot.verification.models import DiffReport, FileChange

    _, _, applier = env

    assert applier.prepare(DiffReport(changes=[]), {}, {}) is None
    with pytest.raises(HostPatchError, match="HOST_PLAN_UNVERIFIED"):
        applier.prepare(
            DiffReport(
                changes=[FileChange(path="app.py", change="modified", authorized=False)]
            ),
            {},
            {},
        )
    with pytest.raises(HostPatchError, match="HOST_PLAN_UNVERIFIED"):
        applier.prepare(
            DiffReport(
                changes=[
                    FileChange(
                        path="app.py", change="modified", binary=True, authorized=True
                    )
                ]
            ),
            {},
            {},
        )


def test_stale_isolated_baseline_conflicts_with_host_snapshot(tmp_path):
    host_root = tmp_path / "host"
    isolated_root = tmp_path / "isolated"
    host_root.mkdir()
    isolated_root.mkdir()
    (host_root / "app.py").write_text("host version\n", encoding="utf-8")
    (isolated_root / "app.py").write_text("stale version\n", encoding="utf-8")
    config = RepositoryConfig(workspace=host_root, max_total_bytes=100000)
    host = Workspace(config)
    isolated = Workspace(config.model_copy(update={"workspace": isolated_root}))
    applier = HostPatchApplier(host, isolated)
    applier.begin_task()
    baseline = WorkspaceSnapshot.capture(isolated, 100000)
    (isolated_root / "app.py").write_text("value = 2\n", encoding="utf-8")
    diff = GitDiffVerifier(isolated, 100000).compare(
        baseline, {"app.py": digest("value = 2\n")}
    )

    with pytest.raises(HostPatchError, match="HOST_CONFLICT"):
        applier.prepare(
            diff,
            {path: digest(text) for path, text in baseline.contents.items()},
            {"app.py": digest("value = 2\n")},
        )


def test_begin_task_fails_closed_on_incomplete_host_snapshot(env, monkeypatch):
    host, _, applier = env
    monkeypatch.setattr(
        host, "list_files", lambda *args, **kwargs: FileListing([], truncated=True)
    )

    with pytest.raises(HostPatchError, match="HOST_SNAPSHOT_INCOMPLETE"):
        applier.begin_task()
