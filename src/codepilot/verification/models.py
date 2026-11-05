from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class VerificationVerdict(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"


class TestExecution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    __test__ = False

    command: list[str] = Field(default_factory=list, max_length=64)
    exit_code: int | None = None
    timed_out: bool = False
    duration_seconds: float = Field(default=0.0, ge=0.0)
    stdout: str = ""
    stderr: str = ""
    output_truncated: bool = False
    output_redacted: bool = False
    error: str | None = Field(default=None, max_length=128)


class FileChange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1, max_length=512)
    change: Literal["added", "modified", "deleted"]
    binary: bool = False
    authorized: bool = False
    additions: int = Field(default=0, ge=0)
    deletions: int = Field(default=0, ge=0)


class DiffReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    changes: list[FileChange] = Field(default_factory=list, max_length=1000)
    diff: str = Field(default="", max_length=64000)
    truncated: bool = False
    incomplete: bool = False
    policy_violations: list[str] = Field(default_factory=list, max_length=32)

    @property
    def unauthorized(self) -> list[str]:
        return sorted(change.path for change in self.changes if not change.authorized)


class VerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    verdict: VerificationVerdict
    reasons: list[str] = Field(default_factory=list, max_length=32)
    test: TestExecution | None = None
    diff: DiffReport
    retryable: bool = False
