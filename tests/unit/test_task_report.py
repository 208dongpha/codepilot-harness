from codepilot.verification.models import (
    DiffReport,
    FileChange,
    TestExecution,
    VerificationResult,
    VerificationVerdict,
)
from codepilot.verification.report import build_task_report


def test_report_summarizes_verification_evidence():
    change = FileChange(
        path="app.py", change="modified", authorized=True, additions=1, deletions=1
    )
    test = TestExecution(
        command=["python", "-m", "pytest"],
        exit_code=0,
        stdout="1 passed\n",
        duration_seconds=0.25,
    )
    verification = VerificationResult(
        verdict=VerificationVerdict.PASS,
        test=test,
        diff=DiffReport(changes=[change]),
    )

    report = build_task_report([verification], retry_count=0)

    assert report.verdict is VerificationVerdict.PASS
    assert report.files_changed == [change]
    assert report.tests_executed == [test]
    assert report.retry_count == 0
    assert report.verification_attempts == 1
    assert report.remaining_issues == []


def test_report_preserves_failure_evidence_and_retry_count():
    failure = TestExecution(
        command=["python", "-m", "pytest"],
        exit_code=1,
        stderr="assertion failed\n",
    )
    verification = VerificationResult(
        verdict=VerificationVerdict.FAIL,
        reasons=["TESTS_FAILED"],
        test=failure,
        diff=DiffReport(),
        retryable=True,
    )

    report = build_task_report([verification], retry_count=1)

    assert report.verdict is VerificationVerdict.FAIL
    assert report.tests_executed == [failure]
    assert report.retry_count == 1
    assert report.verification_attempts == 1
    assert report.remaining_issues == ["TESTS_FAILED"]


def test_report_without_verification_is_inconclusive():
    report = build_task_report([], retry_count=0, state_error="PROVIDER_ERROR")

    assert report.verdict is VerificationVerdict.INCONCLUSIVE
    assert report.files_changed == []
    assert report.tests_executed == []
    assert report.verification_attempts == 0
    assert report.remaining_issues == ["PROVIDER_ERROR"]


def test_report_uses_fallback_diff_when_verification_did_not_run():
    change = FileChange(path="app.py", change="modified", authorized=True)
    report = build_task_report(
        [],
        retry_count=0,
        state_error="PROVIDER_ERROR",
        fallback_diff=DiffReport(changes=[change]),
    )

    assert report.verdict is VerificationVerdict.INCONCLUSIVE
    assert report.files_changed == [change]
    assert report.tests_executed == []
