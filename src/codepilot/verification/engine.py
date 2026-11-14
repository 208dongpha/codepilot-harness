import hashlib
from collections.abc import Mapping
from typing import Protocol

from codepilot.tools.workspace import Workspace
from codepilot.verification.diff import GitDiffVerifier, WorkspaceSnapshot
from codepilot.verification.models import (
    DiffReport,
    TestExecution,
    VerificationResult,
    VerificationVerdict,
)
from codepilot.verification.test_runner import DockerTestRunner


class VerificationError(RuntimeError):
    pass


class Verifier(Protocol):
    def prepare(self) -> None: ...

    def verify(self, authorized: Mapping[str, str | None]) -> VerificationResult: ...

    def diff_report(self, authorized: Mapping[str, str | None]) -> DiffReport: ...

    def baseline_hashes(self) -> Mapping[str, str]: ...


class VerificationEngine:
    """Evaluate configured tests, approved sandbox changes, and policy evidence."""

    def __init__(
        self,
        workspace: Workspace,
        runner: DockerTestRunner,
        differ: GitDiffVerifier,
        max_total_bytes: int,
    ) -> None:
        self.workspace = workspace
        self.runner = runner
        self.differ = differ
        self.max_total_bytes = max_total_bytes
        self._baseline: WorkspaceSnapshot | None = None

    def prepare(self) -> None:
        if self._baseline is not None:
            raise VerificationError("Baseline already prepared.")
        self._baseline = WorkspaceSnapshot.capture(self.workspace, self.max_total_bytes)

    def verify(self, authorized: Mapping[str, str | None]) -> VerificationResult:
        if self._baseline is None:
            raise VerificationError("Baseline has not been prepared.")
        test = self.runner.run(self.workspace.root)
        diff = self.differ.compare(self._baseline, authorized)
        return self._evaluate(test, diff)

    def diff_report(self, authorized: Mapping[str, str | None]) -> DiffReport:
        if self._baseline is None:
            raise VerificationError("Baseline has not been prepared.")
        return self.differ.compare(self._baseline, authorized)

    def baseline_hashes(self) -> Mapping[str, str]:
        if self._baseline is None:
            raise VerificationError("Baseline has not been prepared.")
        return {
            path: hashlib.sha256(text.encode("utf-8")).hexdigest()
            for path, text in self._baseline.contents.items()
        }

    def _evaluate(self, test: TestExecution, diff: DiffReport) -> VerificationResult:
        reasons: list[str] = []
        if diff.policy_violations:
            reasons.extend(diff.policy_violations)
            return VerificationResult(
                verdict=VerificationVerdict.FAIL,
                reasons=reasons,
                test=test,
                diff=diff,
                retryable=False,
            )
        unauthorized = diff.unauthorized
        if unauthorized:
            return VerificationResult(
                verdict=VerificationVerdict.FAIL,
                reasons=["UNAUTHORIZED_CHANGES"],
                test=test,
                diff=diff,
                retryable=False,
            )
        if diff.incomplete:
            return VerificationResult(
                verdict=VerificationVerdict.INCONCLUSIVE,
                reasons=["DIFF_INCOMPLETE"],
                test=test,
                diff=diff,
                retryable=False,
            )
        if test.error is not None:
            return VerificationResult(
                verdict=VerificationVerdict.INCONCLUSIVE,
                reasons=[test.error],
                test=test,
                diff=diff,
                retryable=False,
            )
        if test.timed_out:
            return VerificationResult(
                verdict=VerificationVerdict.INCONCLUSIVE,
                reasons=["TEST_TIMEOUT"],
                test=test,
                diff=diff,
                retryable=False,
            )
        if test.exit_code != 0:
            return VerificationResult(
                verdict=VerificationVerdict.FAIL,
                reasons=["TESTS_FAILED"],
                test=test,
                diff=diff,
                retryable=True,
            )
        return VerificationResult(
            verdict=VerificationVerdict.PASS,
            test=test,
            diff=diff,
            retryable=False,
        )
