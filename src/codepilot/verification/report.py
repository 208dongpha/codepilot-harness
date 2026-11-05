from pydantic import BaseModel, ConfigDict, Field

from codepilot.verification.models import (
    DiffReport,
    FileChange,
    TestExecution,
    VerificationResult,
    VerificationVerdict,
)


class TaskReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    verdict: VerificationVerdict
    files_changed: list[FileChange] = Field(default_factory=list, max_length=1000)
    tests_executed: list[TestExecution] = Field(default_factory=list, max_length=8)
    retry_count: int = Field(default=0, ge=0, le=3)
    verification_attempts: int = Field(default=0, ge=0, le=4)
    remaining_issues: list[str] = Field(default_factory=list, max_length=16)


def build_task_report(
    verifications: list[VerificationResult],
    retry_count: int,
    state_error: str | None = None,
    fallback_diff: DiffReport | None = None,
    forced_reason: str | None = None,
) -> TaskReport:
    latest = verifications[-1] if verifications else None
    if forced_reason is not None:
        issues = [forced_reason]
    else:
        issues = list(latest.reasons) if latest is not None else []
        if state_error is not None and state_error not in issues:
            issues.append(state_error)
    tests = [result.test for result in verifications if result.test is not None]
    if latest is not None:
        files = list(latest.diff.changes)
    elif fallback_diff is not None:
        files = list(fallback_diff.changes)
    else:
        files = []
    if forced_reason is not None:
        verdict = VerificationVerdict.INCONCLUSIVE
    elif latest is not None:
        verdict = latest.verdict
    else:
        verdict = VerificationVerdict.INCONCLUSIVE
    return TaskReport(
        verdict=verdict,
        files_changed=files,
        tests_executed=tests[-8:],
        retry_count=retry_count,
        verification_attempts=len(verifications),
        remaining_issues=issues[:16],
    )
