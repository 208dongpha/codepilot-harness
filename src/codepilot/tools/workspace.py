import os
import re
import stat
import time
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from codepilot.config import RepositoryConfig
from codepilot.tools.base import ToolError

EXCLUDED = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        "node_modules",
        "dist",
        "build",
        ".tox",
        ".cache",
        ".ssh",
        ".aws",
        ".azure",
        ".kube",
        "secrets",
        "credentials",
        "api_key.md",
        "credentials.json",
        "credentials.toml",
        "secrets.json",
        "secrets.toml",
        "id_rsa",
        "id_ed25519",
        ".netrc",
        ".npmrc",
        ".pypirc",
    }
)
SECRET_CONTENT = re.compile(
    r"AIza[0-9A-Za-z_-]{30,}|AQ\.[0-9A-Za-z_-]{30,}|"
    r"(?:gh[pousr]_|github_pat_)[0-9A-Za-z_]{20,}|"
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    r"(?i:(?:api[_-]?key|access[_-]?token|secret|password|authorization)"
    r"[\"']?\s*[:=]\s*[\"'][^\"'\r\n]+[\"'])|"
    r"(?im:^[ \t]*(?:[a-z0-9_]*api[_-]?key|access[_-]?token|secret|password|authorization)"
    r"[ \t]*[:=][ \t]*[a-z0-9_/.+-]+[ \t]*$)"
)


def excluded(part: str) -> bool:
    name = part.casefold()
    return (
        name in EXCLUDED
        or name.startswith((".env", "secret", "credential", "api_key", "api-key"))
        or name.endswith((".pem", ".key", ".p12", ".pfx", ".keystore"))
    )


def linked(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


@dataclass
class FileListing:
    paths: list[str]
    truncated: bool = False
    skipped: int = 0


class Workspace:
    def __init__(self, config: RepositoryConfig):
        self.config = config
        try:
            root = config.workspace.absolute()
            if linked(root.lstat()) or not root.is_dir():
                raise ToolError("INVALID_WORKSPACE")
            self.root = root.resolve(strict=True)
        except OSError:
            raise ToolError("INVALID_WORKSPACE") from None

    def resolve(self, relative: str, *, directory: bool = False) -> Path:
        if not isinstance(relative, str) or len(relative) > 512:
            raise ToolError("ACCESS_DENIED")
        windows = PureWindowsPath(relative)
        if (
            not relative
            or windows.drive
            or windows.root
            or ":" in relative
            or any(ord(c) < 32 for c in relative)
        ):
            raise ToolError("ACCESS_DENIED")
        parts = relative.replace("\\", "/").split("/")
        if (
            ".." in parts
            or any(excluded(part) for part in parts)
            or any(part not in ("", ".") and part != part.rstrip(" .") for part in parts)
        ):
            raise ToolError("ACCESS_DENIED")
        path = self.root
        try:
            for part in parts:
                if part in ("", "."):
                    continue
                path = path / part
                if linked(path.lstat()):
                    raise ToolError("ACCESS_DENIED")
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(self.root):
                raise ToolError("ACCESS_DENIED")
            if any(excluded(part) for part in resolved.relative_to(self.root).parts):
                raise ToolError("ACCESS_DENIED")
            info = resolved.stat()
            if directory:
                if not stat.S_ISDIR(info.st_mode):
                    raise ToolError("NOT_DIRECTORY")
            elif not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
                raise ToolError("ACCESS_DENIED")
            return resolved
        except PermissionError:
            raise ToolError("PERMISSION_DENIED") from None
        except FileNotFoundError:
            raise ToolError("NOT_FOUND") from None
        except (OSError, ValueError):
            raise ToolError("ACCESS_DENIED") from None

    def list_files(self, path: str = ".", limit: int | None = None) -> FileListing:
        base = self.resolve(path, directory=True)
        cap = min(limit or self.config.max_files, self.config.max_files)
        paths: list[str] = []
        pending = [base]
        entries = 0
        depth_truncated = False
        skipped = 0
        deadline = time.monotonic() + self.config.timeout_seconds
        while pending:
            folder = pending.pop()
            relative = folder.relative_to(self.root).as_posix()
            self.resolve(relative, directory=True)
            try:
                with os.scandir(folder) as listing:
                    children = []
                    for entry in listing:
                        entries += 1
                        if entries > self.config.max_scan_entries or time.monotonic() > deadline:
                            return FileListing(sorted(paths), True, skipped)
                        if excluded(entry.name):
                            continue
                        children.append(entry.name)
                for name in sorted(children, reverse=True):
                    child = folder / name
                    try:
                        info = child.lstat()
                        if linked(info):
                            continue
                        relative = child.relative_to(self.root).as_posix()
                        if stat.S_ISDIR(info.st_mode):
                            if len(child.relative_to(self.root).parts) >= self.config.max_depth:
                                depth_truncated = True
                                continue
                            pending.append(child)
                        elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                            self.resolve(relative)
                            if len(paths) >= cap:
                                return FileListing(sorted(paths), True, skipped)
                            paths.append(relative)
                    except (ToolError, OSError):
                        skipped += 1
                        continue
            except PermissionError:
                raise ToolError("PERMISSION_DENIED") from None
            except OSError:
                raise ToolError("READ_ERROR") from None
        return FileListing(sorted(paths), depth_truncated, skipped)

    def read_text(self, relative: str) -> str:
        path = self.resolve(relative)
        try:
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            flags |= getattr(os, "O_NONBLOCK", 0)
            fd = os.open(path, flags)
            with os.fdopen(fd, "rb") as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
                    raise ToolError("ACCESS_DENIED")
                if info.st_size > self.config.max_file_bytes:
                    raise ToolError("FILE_TOO_LARGE")
                data = handle.read(self.config.max_file_bytes + 1)
                if len(data) > self.config.max_file_bytes:
                    raise ToolError("FILE_TOO_LARGE")
                checked = self.resolve(relative).stat()
                if (checked.st_dev, checked.st_ino) != (info.st_dev, info.st_ino):
                    raise ToolError("ACCESS_DENIED")
            if any(byte < 32 and byte not in (9, 10, 13) for byte in data):
                raise ToolError("BINARY_FILE")
            text = data.decode("utf-8-sig", errors="strict")
            if SECRET_CONTENT.search(text):
                raise ToolError("SENSITIVE_CONTENT")
            return text.replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeError:
            raise ToolError("ENCODING_ERROR") from None
        except PermissionError:
            raise ToolError("PERMISSION_DENIED") from None
        except OSError:
            raise ToolError("READ_ERROR") from None
