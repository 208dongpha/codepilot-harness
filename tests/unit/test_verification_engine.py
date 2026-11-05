from pathlib import Path

import pytest

from codepilot.config import RepositoryConfig
from codepilot.tools.workspace import Workspace
from codepilot.verification.diff import GitDiffVerifier
from codepilot.verification.engine import VerificationEngine, VerificationError
from codepilot.verification.models import TestExecution, VerificationVerdict


class FakeRunner:
    def __init__(self, execution: TestExecution) -> None:
        self.execution = execution
        self.calls: list[Path] = []

    def run(self, workspace_root: Path) -> TestExecution:
        self.calls.append(workspace_root)
        return self.execution


def make_engine(tmp_path, execution: TestExecution, *, max_files: int = 1000):
    workspace = Workspace(
        RepositoryConfig(
            workspace=tmp_path,
            max_files=max_files,
            max_total_bytes=100000,
        )
    )
    runner = FakeRunner(execution)
    engine = VerificationEngine(
        workspace=workspace,
        runner=runner,
        differ=GitDiffVerifier(workspace, 100000, max_diff_chars=4000),
        max_total_bytes=100000,
    )
    return workspace, runner, engine


def test_prepare_is_required_and_runs_once(tmp_path):
    (tmp_path / "app.py").write_text("value = 1\n", encoding="utf-8")
    _, runner, engine = make_engine(tmp_path, TestExecution(exit_code=0))

    with pytest.raises(VerificationError):
        engine.verify({})

    engine.prepare()
    with pytest.raises(VerificationError):
        engine.prepare()
    result = engine.verify({})
    assert result.verdict is VerificationVerdict.PASS
    assert runner.calls == [tmp_path]


def test_passing_tests_and_no_changes_pass(tmp_path):
    (tmp_path / "app.py").write_text("value = 1\n", encoding="utf-8")
    _, _, engine = make_engine(
        tmp_path,
        TestExecution(command=["python", "-m", "pytest"], exit_code=0, stdout="1 passed\n"),
    )
    engine.prepare()

    result = engine.verify({})

    assert result.verdict is VerificationVerdict.PASS
    assert result.reasons == []
    assert result.retryable is False


def test_test_failure_is_retryable(tmp_path):
    (tmp_path / "app.py").write_text("value = 1\n", encoding="utf-8")
    _, _, engine = make_engine(
        tmp_path,
        TestExecution(command=["python", "-m", "pytest"], exit_code=1, stderr="failure\n"),
    )
    engine.prepare()

    result = engine.verify({})

    assert result.verdict is VerificationVerdict.FAIL
    assert result.reasons == ["TESTS_FAILED"]
    assert result.retryable is True


def test_timeout_is_inconclusive(tmp_path):
    (tmp_path / "app.py").write_text("value = 1\n", encoding="utf-8")
    execution = TestExecution(timed_out=True, exit_code=None)
    _, _, engine = make_engine(tmp_path, execution)
    engine.prepare()

    result = engine.verify({})

    assert result.verdict is VerificationVerdict.INCONCLUSIVE
    assert result.reasons == ["TEST_TIMEOUT"]
    assert not result.retryable


def test_sandbox_error_is_inconclusive(tmp_path):
    (tmp_path / "app.py").write_text("value = 1\n", encoding="utf-8")
    _, _, engine = make_engine(
        tmp_path, TestExecution(error="SANDBOX_UNAVAILABLE")
    )
    engine.prepare()

    result = engine.verify({})

    assert result.verdict is VerificationVerdict.INCONCLUSIVE
    assert result.reasons == ["SANDBOX_UNAVAILABLE"]
    assert not result.retryable


def test_unauthorized_change_fails_without_retry(tmp_path):
    (tmp_path / "app.py").write_text("value = 1\n", encoding="utf-8")
    _, _, engine = make_engine(tmp_path, TestExecution(exit_code=0))
    engine.prepare()
    (tmp_path / "app.py").write_text("tampered\n", encoding="utf-8")

    result = engine.verify({})

    assert result.verdict is VerificationVerdict.FAIL
    assert result.reasons == ["UNAUTHORIZED_CHANGES"]
    assert result.diff.unauthorized == ["app.py"]
    assert not result.retryable


def test_policy_violation_takes_precedence(tmp_path):
    (tmp_path / "app.py").write_text("value = 1\n", encoding="utf-8")
    _, _, engine = make_engine(tmp_path, TestExecution(exit_code=1))
    engine.prepare()
    (tmp_path / "leak.py").write_text(
        'password = "synthetic-private-value"\n', encoding="utf-8"
    )

    result = engine.verify({})

    assert result.verdict is VerificationVerdict.FAIL
    assert "SENSITIVE_CONTENT_DETECTED" in result.reasons
    assert not result.retryable


def test_incomplete_diff_is_inconclusive(tmp_path):
    (tmp_path / "a.py").write_text("a = 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("b = 1\n", encoding="utf-8")
    _, _, engine = make_engine(tmp_path, TestExecution(exit_code=0), max_files=1)
    engine.prepare()

    result = engine.verify({})

    assert result.verdict is VerificationVerdict.INCONCLUSIVE
    assert result.reasons == ["DIFF_INCOMPLETE"]
    assert not result.retryable


def test_added_symlink_fails_verification_without_following(tmp_path):
    (tmp_path / "app.py").write_text("value = 1\n", encoding="utf-8")
    _, _, engine = make_engine(tmp_path, TestExecution(exit_code=0))
    engine.prepare()
    outside = tmp_path.parent / f"{tmp_path.name}-outside.py"
    outside.write_text("outside\n", encoding="utf-8")
    try:
        (tmp_path / "alias.py").symlink_to(outside)
    except OSError:
        pytest.skip("Symlink creation is unavailable.")

    result = engine.verify({})

    assert result.verdict is VerificationVerdict.FAIL
    assert "LINKED_ENTRY_DETECTED" in result.reasons
    assert result.diff.unauthorized == ["alias.py"]
    assert not result.retryable
