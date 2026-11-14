import hashlib

import pytest

from codepilot.config import RepositoryConfig
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
