"""Exact, single-use host patch application for verified final diffs.

The host is never modified while planning or approving. Applying revalidates every affected
host file against a task-start snapshot, writes through same-directory temporary files, and
restores the original files if any write fails.
"""

import hashlib
import json
import os
import stat
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Literal

from codepilot.patch.tool import PatchError, safe_parts
from codepilot.tools.base import ToolError
from codepilot.tools.workspace import Workspace, linked
from codepilot.verification.models import DiffReport


class HostPatchError(RuntimeError):
    pass


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _omitted(exc: ToolError) -> bool:
    return exc.code in {"BINARY_FILE", "FILE_TOO_LARGE", "SENSITIVE_CONTENT"}


@dataclass(frozen=True)
class HostPatchEntry:
    path: str
    change: Literal["added", "modified", "deleted"]
    expected_sha256: str | None
    new_sha256: str | None
    content: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class HostPatchPlan:
    plan_id: str
    diff: str
    entries: tuple[HostPatchEntry, ...]

    @property
    def files(self) -> tuple[str, ...]:
        return tuple(entry.path for entry in self.entries)


@dataclass(frozen=True)
class HostApplyResult:
    plan_id: str
    files: tuple[str, ...]


@dataclass
class _PendingApply:
    plan: HostPatchPlan
    expires_at: float
    approved: bool = False
    used: bool = False


class HostPatchApplier:
    def __init__(
        self,
        host: Workspace,
        isolated: Workspace,
        ttl_seconds: float = 300,
        clock=time.monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("Host approval TTL must be positive.")
        self.host = host
        self.isolated = isolated
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._snapshot: dict[str, str] | None = None
        self._pending: _PendingApply | None = None
        self._created_dirs: list[Path] = []
        self._lock = Lock()

    @property
    def pending_plan(self) -> HostPatchPlan | None:
        return self._pending.plan if self._pending is not None else None

    def begin_task(self) -> None:
        """Capture the host state that a later verified diff must be based on."""
        self._pending = None
        listing = self.host.list_files()
        if listing.truncated or listing.skipped:
            raise HostPatchError("HOST_SNAPSHOT_INCOMPLETE")
        snapshot: dict[str, str] = {}
        total = 0
        for path in listing.paths:
            try:
                content = self.host.read_text(path)
            except ToolError as exc:
                if _omitted(exc):
                    continue
                raise HostPatchError("HOST_SNAPSHOT_INCOMPLETE") from None
            total += len(content.encode("utf-8"))
            if total > self.host.config.max_total_bytes:
                raise HostPatchError("HOST_SNAPSHOT_INCOMPLETE")
            snapshot[path] = _sha256(content)
        self._snapshot = snapshot

    def prepare(
        self,
        diff: DiffReport,
        baseline_hashes: Mapping[str, str],
        authorized_hashes: Mapping[str, str | None],
    ) -> HostPatchPlan | None:
        if self._snapshot is None:
            raise HostPatchError("HOST_SNAPSHOT_MISSING")
        if not diff.changes:
            return None
        if (
            diff.incomplete
            or diff.policy_violations
            or diff.unauthorized
            or diff.truncated
        ):
            raise HostPatchError("HOST_PLAN_UNVERIFIED")
        entries: list[HostPatchEntry] = []
        total = 0
        for change in diff.changes:
            if not change.authorized or change.binary:
                raise HostPatchError("HOST_PLAN_UNVERIFIED")
            expected = self._snapshot.get(change.path)
            if baseline_hashes.get(change.path) != expected:
                raise HostPatchError("HOST_CONFLICT")
            try:
                if change.change == "added":
                    if expected is not None or self._host_exists(change.path):
                        raise HostPatchError("HOST_CONFLICT")
                    content = self.isolated.read_text(change.path)
                    new_sha256 = _sha256(content)
                    if authorized_hashes.get(change.path) != new_sha256:
                        raise HostPatchError("HOST_PLAN_UNVERIFIED")
                    entry = HostPatchEntry(change.path, "added", None, new_sha256, content)
                elif change.change == "deleted":
                    if expected is None or self._host_text(change.path)[0] != expected:
                        raise HostPatchError("HOST_CONFLICT")
                    if (
                        change.path not in authorized_hashes
                        or authorized_hashes[change.path] is not None
                    ):
                        raise HostPatchError("HOST_PLAN_UNVERIFIED")
                    entry = HostPatchEntry(change.path, "deleted", expected, None)
                else:
                    if expected is None or self._host_text(change.path)[0] != expected:
                        raise HostPatchError("HOST_CONFLICT")
                    content = self.isolated.read_text(change.path)
                    new_sha256 = _sha256(content)
                    if authorized_hashes.get(change.path) != new_sha256:
                        raise HostPatchError("HOST_PLAN_UNVERIFIED")
                    entry = HostPatchEntry(
                        change.path, "modified", expected, new_sha256, content
                    )
            except ToolError:
                raise HostPatchError("HOST_PLAN_UNVERIFIED") from None
            total += len((entry.content or "").encode("utf-8"))
            if total > self.isolated.config.max_total_bytes:
                raise HostPatchError("HOST_PLAN_TOO_LARGE")
            entries.append(entry)
        plan_id = _plan_id(diff.diff, entries)
        plan = HostPatchPlan(plan_id, diff.diff, tuple(entries))
        self._pending = _PendingApply(plan, self._clock() + self.ttl_seconds)
        return plan

    def approve(self, plan_id: str) -> None:
        pending = self._require(plan_id)
        self._ensure_active(pending)
        pending.approved = True

    def discard(self) -> None:
        self._pending = None

    def apply(self, plan_id: str) -> HostApplyResult:
        if not self._lock.acquire(blocking=False):
            raise HostPatchError("HOST_APPLY_BUSY")
        try:
            pending = self._require(plan_id)
            self._ensure_active(pending)
            if not pending.approved:
                raise HostPatchError("HOST_APPROVAL_REQUIRED")
            pending.used = True
            plan = pending.plan
            for entry in plan.entries:
                if entry.content is not None and _sha256(entry.content) != entry.new_sha256:
                    raise HostPatchError("HOST_PLAN_TAMPERED")
            backups = self._recheck(plan)
            self._created_dirs = []
            try:
                self._commit(plan)
                for entry in plan.entries:
                    if entry.change == "deleted":
                        if self._host_exists(entry.path):
                            raise HostPatchError("HOST_APPLY_FAILED")
                    elif self._host_text(entry.path)[0] != entry.new_sha256:
                        raise HostPatchError("HOST_APPLY_FAILED")
            except Exception as exc:
                self._rollback(plan, backups)
                if isinstance(exc, HostPatchError) and str(exc) in {
                    "HOST_ROLLBACK_FAILED",
                    "HOST_PLAN_TAMPERED",
                }:
                    raise
                raise HostPatchError("HOST_APPLY_FAILED") from None
            self._pending = None
            return HostApplyResult(plan.plan_id, plan.files)
        finally:
            self._lock.release()

    def _recheck(self, plan: HostPatchPlan) -> dict[str, tuple[str, int]]:
        backups: dict[str, tuple[str, int]] = {}
        for entry in plan.entries:
            exists = self._host_exists(entry.path)
            if entry.expected_sha256 is None:
                if exists:
                    raise HostPatchError("HOST_CONFLICT")
                continue
            if not exists:
                raise HostPatchError("HOST_CONFLICT")
            current, content = self._host_text(entry.path)
            if current != entry.expected_sha256:
                raise HostPatchError("HOST_CONFLICT")
            backups[entry.path] = (content, self._host_mode(entry.path))
        return backups

    def _commit(self, plan: HostPatchPlan) -> None:
        for entry in plan.entries:
            if entry.change == "deleted":
                self._delete(entry.path)
            else:
                self._write_entry(entry)

    def _write_entry(self, entry: HostPatchEntry) -> None:
        target, created = self._prepare_target(entry.path)
        self._created_dirs.extend(created)
        mode = 0o644 if entry.change == "added" else self._host_mode(entry.path)
        self._atomic_write(target, entry.content or "", mode)

    def _rollback(self, plan: HostPatchPlan, backups: dict[str, tuple[str, int]]) -> None:
        failures: list[str] = []
        for entry in reversed(plan.entries):
            try:
                if entry.change == "added":
                    if self._host_exists(entry.path):
                        os.unlink(self.host.root.joinpath(*safe_parts(entry.path)))
                else:
                    content, mode = backups[entry.path]
                    target = self.host.root.joinpath(*safe_parts(entry.path))
                    self._atomic_write(target, content, mode)
            except (HostPatchError, OSError, KeyError, PatchError):
                failures.append(entry.path)
        for directory in reversed(self._created_dirs):
            try:
                directory.rmdir()
            except OSError:
                pass
        for entry in plan.entries:
            try:
                if entry.expected_sha256 is None:
                    if self._host_exists(entry.path):
                        failures.append(entry.path)
                elif not self._host_exists(entry.path):
                    failures.append(entry.path)
                elif self._host_text(entry.path)[0] != entry.expected_sha256:
                    failures.append(entry.path)
            except HostPatchError:
                failures.append(entry.path)
        if failures:
            raise HostPatchError("HOST_ROLLBACK_FAILED") from None

    def _delete(self, path: str) -> None:
        try:
            target = self.host.root.joinpath(*safe_parts(path))
            info = target.lstat()
            if linked(info) or not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
                raise HostPatchError("HOST_CONFLICT")
            target.unlink()
        except (OSError, PatchError):
            raise HostPatchError("HOST_APPLY_FAILED") from None

    def _prepare_target(self, path: str) -> tuple[Path, list[Path]]:
        try:
            parts = safe_parts(path)
        except PatchError:
            raise HostPatchError("HOST_CONFLICT") from None
        current = self.host.root
        created: list[Path] = []
        try:
            for part in parts[:-1]:
                current = current / part
                if not os.path.lexists(current):
                    os.mkdir(current)
                    created.append(current)
                info = current.lstat()
                if linked(info) or not stat.S_ISDIR(info.st_mode):
                    raise HostPatchError("HOST_CONFLICT")
                if not current.resolve(strict=True).is_relative_to(self.host.root):
                    raise HostPatchError("HOST_CONFLICT")
            target = self.host.root.joinpath(*parts)
            if os.path.lexists(target):
                info = target.lstat()
                if linked(info) or not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
                    raise HostPatchError("HOST_CONFLICT")
            return target, created
        except HostPatchError:
            for directory in reversed(created):
                try:
                    directory.rmdir()
                except OSError:
                    pass
            raise
        except OSError:
            raise HostPatchError("HOST_APPLY_FAILED") from None

    def _atomic_write(self, target: Path, content: str, mode: int) -> None:
        fd, temporary = tempfile.mkstemp(prefix=".codepilot-apply-", dir=target.parent)
        temp_path = Path(temporary)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content.encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_path, mode & 0o777)
            os.replace(temp_path, target)
        except OSError:
            raise HostPatchError("HOST_APPLY_FAILED") from None
        finally:
            temp_path.unlink(missing_ok=True)

    def _host_exists(self, path: str) -> bool:
        try:
            return os.path.lexists(self.host.root.joinpath(*safe_parts(path)))
        except PatchError:
            raise HostPatchError("HOST_CONFLICT") from None

    def _host_text(self, path: str) -> tuple[str, str]:
        try:
            content = self.host.read_text(path)
        except ToolError:
            raise HostPatchError("HOST_CONFLICT") from None
        return _sha256(content), content

    def _host_mode(self, path: str) -> int:
        try:
            info = self.host.resolve(path).stat()
        except ToolError:
            raise HostPatchError("HOST_CONFLICT") from None
        return stat.S_IMODE(info.st_mode) & 0o777

    def _require(self, plan_id: str) -> _PendingApply:
        if self._pending is None or self._pending.plan.plan_id != plan_id:
            raise HostPatchError("HOST_APPROVAL_MISMATCH")
        return self._pending

    def _ensure_active(self, pending: _PendingApply) -> None:
        if pending.used:
            raise HostPatchError("HOST_APPROVAL_USED")
        if self._clock() > pending.expires_at:
            raise HostPatchError("HOST_APPROVAL_EXPIRED")


def _plan_id(diff: str, entries: list[HostPatchEntry]) -> str:
    canonical = json.dumps(
        {
            "diff_sha256": _sha256(diff),
            "entries": [
                {
                    "change": entry.change,
                    "expected_sha256": entry.expected_sha256,
                    "new_sha256": entry.new_sha256,
                    "path": entry.path,
                }
                for entry in entries
            ],
        },
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return _sha256(canonical)
