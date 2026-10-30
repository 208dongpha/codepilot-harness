import difflib
import hashlib
import os
import stat
import tempfile
from pathlib import Path, PureWindowsPath
from threading import Lock
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field

from codepilot.permissions.engine import Action, PermissionEngine
from codepilot.tools.base import ToolError
from codepilot.tools.workspace import SECRET_CONTENT, Workspace, excluded, linked


class PatchError(RuntimeError):
    pass


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _safe_parts(relative: str) -> tuple[str, ...]:
    windows = PureWindowsPath(relative)
    parts = tuple(relative.replace("\\", "/").split("/"))
    if (
        not relative
        or len(relative) > 512
        or windows.drive
        or windows.root
        or ":" in relative
        or ".." in parts
        or any(part in ("", ".") or excluded(part) for part in parts)
        or any(part != part.rstrip(" .") for part in parts)
        or any(ord(char) < 32 for char in relative)
    ):
        raise PatchError("UNSAFE_TARGET")
    return parts


class IsolatedWorkspace:
    def __init__(self, root: Path, temporary: tempfile.TemporaryDirectory) -> None:
        self.root = root
        self._temporary = temporary
        self._closed = False

    @classmethod
    def create(cls, host: Workspace) -> "IsolatedWorkspace":
        temporary = tempfile.TemporaryDirectory(prefix="codepilot-isolated-")
        temp_root = Path(temporary.name)
        root = temp_root / "workspace"
        root.mkdir(mode=0o700)
        try:
            if root.resolve().is_relative_to(host.root):
                raise PatchError("INVALID_ISOLATION")
            for relative in host.list_files().paths:
                try:
                    content = host.read_text(relative)
                except ToolError:
                    continue
                target = root.joinpath(*_safe_parts(relative))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8", newline="")
            return cls(root.resolve(strict=True), temporary)
        except Exception:
            temporary.cleanup()
            raise

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        if not self._closed:
            self._temporary.cleanup()
            self._closed = True

    def __enter__(self) -> "IsolatedWorkspace":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class PatchProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation: Literal["create", "replace", "delete"]
    target: str
    expected_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    content: str | None = None
    diff: str = Field(max_length=16000)

    @computed_field
    @property
    def action(self) -> Action:
        return Action(
            tool="apply_patch",
            operation=self.operation,
            target=self.target,
            arguments={
                "expected_sha256": self.expected_sha256,
                "content_sha256": _sha256(self.content or ""),
            },
        )


class PatchResult(BaseModel):
    operation: str
    target: str
    sha256: str | None = None


class PatchTool:
    def __init__(
        self,
        workspace: IsolatedWorkspace,
        permissions: PermissionEngine,
        max_content_chars: int = 131072,
    ) -> None:
        self.workspace = workspace
        self.permissions = permissions
        self.max_content_chars = max_content_chars
        self._lock = Lock()

    def propose_replace(self, target: str, content: str) -> PatchProposal:
        current = self._read_existing(target)
        return self._proposal("replace", target, current, content)

    def propose_create(self, target: str, content: str) -> PatchProposal:
        path = self._target(target, allow_missing=True)
        if path.exists():
            raise PatchError("PATCH_CONFLICT")
        return self._proposal("create", target, None, content)

    def propose_delete(self, target: str) -> PatchProposal:
        current = self._read_existing(target)
        return self._proposal("delete", target, current, None)

    def apply(self, proposal: PatchProposal) -> PatchResult:
        if self.workspace.closed:
            raise PatchError("WORKSPACE_CLOSED")
        if not self._lock.acquire(blocking=False):
            raise PatchError("PATCH_BUSY")
        try:
            self.permissions.consume(proposal.action)
            path = self._target(proposal.target, allow_missing=proposal.operation == "create")
            if proposal.operation == "create":
                if path.exists():
                    raise PatchError("PATCH_CONFLICT")
                path.parent.mkdir(parents=True, exist_ok=True)
                self._write_atomic(path, proposal.content or "", create=True)
                return PatchResult(
                    operation="create",
                    target=proposal.target,
                    sha256=_sha256(proposal.content or ""),
                )
            current = self._read_existing(proposal.target)
            if _sha256(current) != proposal.expected_sha256:
                raise PatchError("STALE_PATCH")
            if proposal.operation == "delete":
                path.unlink()
                return PatchResult(operation="delete", target=proposal.target)
            self._write_atomic(
                path,
                proposal.content or "",
                create=False,
                expected_sha256=proposal.expected_sha256,
            )
            return PatchResult(
                operation="replace", target=proposal.target, sha256=_sha256(proposal.content or "")
            )
        finally:
            self._lock.release()

    def _proposal(
        self,
        operation: Literal["create", "replace", "delete"],
        target: str,
        current: str | None,
        content: str | None,
    ) -> PatchProposal:
        _safe_parts(target)
        if content is not None:
            if len(content) > self.max_content_chars:
                raise PatchError("OUTPUT_LIMIT")
            if SECRET_CONTENT.search(content):
                raise PatchError("SENSITIVE_CONTENT")
        old_lines = (current or "").splitlines(keepends=True)
        new_lines = (content or "").splitlines(keepends=True)
        diff = "".join(
            difflib.unified_diff(old_lines, new_lines, fromfile=f"a/{target}", tofile=f"b/{target}")
        )
        if len(diff) > 16000:
            diff = diff[:15968] + "\n[DIFF_TRUNCATED]\n"
        return PatchProposal(
            operation=operation,
            target=target,
            expected_sha256=_sha256(current) if current is not None else None,
            content=content,
            diff=diff,
        )

    def _target(self, relative: str, *, allow_missing: bool = False) -> Path:
        parts = _safe_parts(relative)
        path = self.workspace.root
        try:
            for index, part in enumerate(parts):
                path = path / part
                if not path.exists() and allow_missing:
                    if index < len(parts) - 1:
                        continue
                    break
                info = path.lstat()
                if linked(info):
                    raise PatchError("UNSAFE_TARGET")
            resolved_parent = path.parent.resolve(strict=True)
            if not resolved_parent.is_relative_to(self.workspace.root):
                raise PatchError("UNSAFE_TARGET")
            if path.exists():
                info = path.stat()
                if not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
                    raise PatchError("UNSAFE_TARGET")
            return path
        except (OSError, ValueError):
            raise PatchError("UNSAFE_TARGET") from None

    def _read_existing(self, relative: str) -> str:
        path = self._target(relative)
        try:
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(path, flags)
            with os.fdopen(fd, "rb") as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
                    raise PatchError("UNSAFE_TARGET")
                data = handle.read(self.max_content_chars * 4 + 1)
            if len(data) > self.max_content_chars * 4:
                raise PatchError("FILE_TOO_LARGE")
            checked = self._target(relative).stat()
            if (checked.st_dev, checked.st_ino) != (info.st_dev, info.st_ino):
                raise PatchError("STALE_PATCH")
            content = data.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
            if SECRET_CONTENT.search(content):
                raise PatchError("SENSITIVE_CONTENT")
            return content
        except UnicodeError:
            raise PatchError("ENCODING_ERROR") from None
        except FileNotFoundError:
            raise PatchError("PATCH_CONFLICT") from None
        except OSError:
            raise PatchError("READ_ERROR") from None

    def _write_atomic(
        self,
        target: Path,
        content: str,
        create: bool,
        expected_sha256: str | None = None,
    ) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".codepilot-", dir=target.parent)
        temp_path = Path(temporary)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content.encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
            if create and target.exists():
                raise PatchError("PATCH_CONFLICT")
            if not create:
                relative = target.relative_to(self.workspace.root).as_posix()
                if _sha256(self._read_existing(relative)) != expected_sha256:
                    raise PatchError("STALE_PATCH")
            os.replace(temp_path, target)
        except OSError:
            raise PatchError("WRITE_ERROR") from None
        finally:
            temp_path.unlink(missing_ok=True)
