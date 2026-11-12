import hashlib

import pytest

from codepilot.config import RepositoryConfig
from codepilot.patch.tool import IsolatedWorkspace
from codepilot.tools.base import ToolError
from codepilot.tools.workspace import Workspace
from codepilot.verification.diff import GitDiffVerifier, WorkspaceSnapshot


def digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@pytest.fixture
def diff_harness(tmp_path):
    host = tmp_path / "host"
    host.mkdir()
    (host / "app.py").write_text("value = 1\n", encoding="utf-8")
    (host / "pkg").mkdir()
    (host / "pkg" / "module.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    isolated = IsolatedWorkspace.create(Workspace(RepositoryConfig(workspace=host)))
    config = RepositoryConfig(workspace=isolated.root, max_total_bytes=100000)
    workspace = Workspace(config)
    verifier = GitDiffVerifier(workspace, config.max_total_bytes, max_diff_chars=4000)
    baseline = WorkspaceSnapshot.capture(workspace, config.max_total_bytes)
    try:
        yield workspace, verifier, baseline
    finally:
        isolated.close()


def test_unchanged_workspace_has_no_changes(diff_harness):
    _, verifier, baseline = diff_harness

    report = verifier.compare(baseline, {})

    assert report.changes == []
    assert report.diff == ""
    assert report.unauthorized == []
    assert not report.incomplete


def test_authorized_replace_and_create_produce_git_style_diff(diff_harness):
    workspace, verifier, baseline = diff_harness
    (workspace.root / "app.py").write_text("value = 2\n", encoding="utf-8")
    (workspace.root / "new.py").write_text("created\n", encoding="utf-8")

    report = verifier.compare(
        baseline,
        {"app.py": digest("value = 2\n"), "new.py": digest("created\n")},
    )

    assert report.unauthorized == []
    changes = {change.path: change for change in report.changes}
    assert changes["app.py"].change == "modified"
    assert changes["app.py"].authorized
    assert changes["app.py"].additions == 1
    assert changes["app.py"].deletions == 1
    assert changes["new.py"].change == "added"
    assert changes["new.py"].authorized
    assert "--- a/app.py" in report.diff
    assert "+++ b/app.py" in report.diff
    assert "+value = 2" in report.diff


def test_authorized_hash_mismatch_is_untrusted(diff_harness):
    workspace, verifier, baseline = diff_harness
    (workspace.root / "app.py").write_text("tampered\n", encoding="utf-8")

    report = verifier.compare(baseline, {"app.py": digest("approved\n")})

    assert report.unauthorized == ["app.py"]
    assert not report.changes[0].authorized


def test_unauthorized_modify_add_and_delete_are_detected(diff_harness):
    workspace, verifier, baseline = diff_harness
    (workspace.root / "app.py").write_text("changed\n", encoding="utf-8")
    (workspace.root / "extra.py").write_text("extra\n", encoding="utf-8")
    (workspace.root / "pkg" / "module.py").unlink()

    report = verifier.compare(baseline, {})

    changes = {change.path: change for change in report.changes}
    assert changes["app.py"].change == "modified"
    assert changes["extra.py"].change == "added"
    assert changes["pkg/module.py"].change == "deleted"
    assert report.unauthorized == ["app.py", "extra.py", "pkg/module.py"]
    assert "-def f():" in report.diff


def test_authorized_delete_requires_none_expected_hash(diff_harness):
    workspace, verifier, baseline = diff_harness
    (workspace.root / "app.py").unlink()

    report = verifier.compare(baseline, {"app.py": None})

    assert report.unauthorized == []
    assert report.changes[0].change == "deleted"
    assert report.changes[0].authorized


def test_binary_changes_are_reported_without_reading_content(diff_harness):
    workspace, verifier, baseline = diff_harness
    (workspace.root / "artifact.bin").write_bytes(b"\x00\x01\x02")

    report = verifier.compare(baseline, {})

    change = next(item for item in report.changes if item.path == "artifact.bin")
    assert change.change == "added"
    assert change.binary
    assert not change.authorized
    assert "artifact.bin" in report.unauthorized


def test_sensitive_content_is_a_policy_violation(diff_harness):
    workspace, verifier, baseline = diff_harness
    (workspace.root / "leak.py").write_text(
        'api_key = "synthetic-private-value"\n', encoding="utf-8"
    )

    report = verifier.compare(baseline, {})

    assert "SENSITIVE_CONTENT_DETECTED" in report.policy_violations
    assert "leak.py" in report.unauthorized


def test_diff_output_is_bounded(diff_harness):
    workspace, _, baseline = diff_harness
    verifier = GitDiffVerifier(workspace, 100000, max_diff_chars=512)
    (workspace.root / "app.py").write_text(
        "\n".join(f"value = {index}" for index in range(200)) + "\n",
        encoding="utf-8",
    )

    report = verifier.compare(baseline, {})

    assert report.truncated
    assert len(report.diff) <= 512
    assert "DIFF_TRUNCATED" in report.diff


def test_incomplete_baseline_fails_closed(tmp_path):
    host = tmp_path / "host"
    host.mkdir()
    (host / "a.py").write_text("a = 1\n", encoding="utf-8")
    (host / "b.py").write_text("b = 1\n", encoding="utf-8")
    config = RepositoryConfig(workspace=host, max_files=1, max_total_bytes=100000)
    workspace = Workspace(config)
    baseline = WorkspaceSnapshot.capture(workspace, config.max_total_bytes)
    assert not baseline.complete

    report = GitDiffVerifier(workspace, config.max_total_bytes).compare(baseline, {})

    assert report.incomplete
    assert report.changes == []


def test_workspace_list_files_reports_linked_entries_without_following(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "app.py").write_text("value = 1\n", encoding="utf-8")
    outside = tmp_path / "outside.py"
    outside.write_text("outside\n", encoding="utf-8")
    try:
        (root / "alias.py").symlink_to(outside)
    except OSError:
        pytest.skip("Symlink creation is unavailable.")

    listing = Workspace(RepositoryConfig(workspace=root)).list_files()

    assert listing.paths == ["app.py"]
    assert listing.linked_paths == ["alias.py"]


def test_added_symlink_is_reported_and_fails_closed(diff_harness, tmp_path):
    workspace, verifier, baseline = diff_harness
    outside = tmp_path / "outside.py"
    outside.write_text("outside\n", encoding="utf-8")
    try:
        (workspace.root / "alias.py").symlink_to(outside)
    except OSError:
        pytest.skip("Symlink creation is unavailable.")

    report = verifier.compare(baseline, {})

    assert "LINKED_ENTRY_DETECTED" in report.policy_violations
    assert report.incomplete
    assert "alias.py" in report.unauthorized
    change = next(item for item in report.changes if item.path == "alias.py")
    assert change.change == "added"
    assert change.binary
    assert not change.authorized


def test_replaced_file_with_symlink_is_reported_and_fails_closed(diff_harness, tmp_path):
    workspace, verifier, baseline = diff_harness
    outside = tmp_path / "outside.py"
    outside.write_text("outside\n", encoding="utf-8")
    target = workspace.root / "app.py"
    target.unlink()
    try:
        target.symlink_to(outside)
    except OSError:
        pytest.skip("Symlink creation is unavailable.")

    report = verifier.compare(baseline, {})

    assert "LINKED_ENTRY_DETECTED" in report.policy_violations
    assert report.incomplete
    assert "app.py" in report.unauthorized
    change = next(item for item in report.changes if item.path == "app.py")
    assert change.change == "modified"
    assert change.binary
    assert not change.authorized


def test_excluded_entries_are_reported_as_metadata_only(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "app.py").write_text("value = 1\n", encoding="utf-8")
    (root / ".env").write_text("API_KEY=synthetic-private-value\n", encoding="utf-8")
    (root / "secrets").mkdir()
    (root / "secrets" / "prod.env").write_text(
        "TOKEN=synthetic-private-value\n", encoding="utf-8"
    )
    workspace = Workspace(RepositoryConfig(workspace=root))

    listing = workspace.list_files()

    assert listing.paths == ["app.py"]
    assert [entry.path for entry in listing.excluded] == [
        ".env",
        "secrets",
        "secrets/prod.env",
    ]
    with pytest.raises(ToolError):
        workspace.read_text(".env")


def test_new_excluded_file_fails_closed(diff_harness):
    workspace, verifier, baseline = diff_harness
    (workspace.root / ".env").write_text(
        "API_KEY=synthetic-private-value\n", encoding="utf-8"
    )

    report = verifier.compare(baseline, {})

    assert "EXCLUDED_ENTRY_ADDED" in report.policy_violations
    assert ".env" in report.unauthorized
    change = next(item for item in report.changes if item.path == ".env")
    assert change.change == "added"
    assert change.binary
    assert not change.authorized
    assert "synthetic-private-value" not in report.diff


def test_new_excluded_directory_fails_closed(diff_harness):
    workspace, verifier, baseline = diff_harness
    secrets = workspace.root / "secrets"
    secrets.mkdir()
    (secrets / "token.txt").write_text("synthetic-private-value\n", encoding="utf-8")

    report = verifier.compare(baseline, {})

    assert "EXCLUDED_ENTRY_ADDED" in report.policy_violations
    assert {"secrets", "secrets/token.txt"} <= set(report.unauthorized)
    assert "synthetic-private-value" not in report.diff


def test_modified_baseline_excluded_entry_fails_closed(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "app.py").write_text("value = 1\n", encoding="utf-8")
    (root / "secrets").mkdir()
    secret = root / "secrets" / "prod.env"
    secret.write_text("TOKEN=old\n", encoding="utf-8")
    config = RepositoryConfig(workspace=root, max_total_bytes=100000)
    workspace = Workspace(config)
    baseline = WorkspaceSnapshot.capture(workspace, config.max_total_bytes)
    secret.write_text("TOKEN=new-and-longer\n", encoding="utf-8")

    report = GitDiffVerifier(workspace, config.max_total_bytes).compare(baseline, {})

    assert "EXCLUDED_ENTRY_MODIFIED" in report.policy_violations
    change = next(item for item in report.changes if item.path == "secrets/prod.env")
    assert change.change == "modified"
    assert change.binary
    assert not change.authorized
    assert "new-and-longer" not in report.diff


def test_stable_baseline_excluded_entry_stays_clean(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "app.py").write_text("value = 1\n", encoding="utf-8")
    (root / "secrets").mkdir()
    (root / "secrets" / "prod.env").write_text("TOKEN=synthetic\n", encoding="utf-8")
    config = RepositoryConfig(workspace=root, max_total_bytes=100000)
    workspace = Workspace(config)
    baseline = WorkspaceSnapshot.capture(workspace, config.max_total_bytes)

    report = GitDiffVerifier(workspace, config.max_total_bytes).compare(baseline, {})

    assert report.changes == []
    assert report.policy_violations == []
    assert not report.incomplete


def test_tooling_cache_churn_is_not_a_policy_violation(diff_harness):
    workspace, verifier, baseline = diff_harness
    cache = workspace.root / ".pytest_cache" / "v"
    cache.mkdir(parents=True)
    (cache / "cache.json").write_text("{}\n", encoding="utf-8")

    report = verifier.compare(baseline, {})

    assert report.changes == []
    assert report.policy_violations == []
    assert not report.incomplete
