"""Race-resistant mutation of one host file inside its parent directory.

POSIX uses directory file descriptors and *at() operations, so the parent path may be renamed or
replaced without redirecting the write. Windows holds no-delete-share directory handles for every
component, which blocks rename/delete of those components, and opens directories with
FILE_FLAG_OPEN_REPARSE_POINT so junctions and symlinks are rejected. Unsupported platforms fail
closed through GuardUnsupported.
"""

import ctypes
import errno
import hashlib
import os
import stat
import tempfile
import uuid
from pathlib import Path

_HAS_DIR_FD = os.name != "nt" and all(
    function in os.supports_dir_fd
    for function in (os.open, os.mkdir, os.rename, os.unlink, os.stat)
)

_WINDOWS_FILE_LIST_DIRECTORY = 0x00000001
_WINDOWS_FILE_READ_ATTRIBUTES = 0x00000080
_WINDOWS_GENERIC_READ = 0x80000000
_WINDOWS_SYNCHRONIZE = 0x00100000
_WINDOWS_SHARE_READ = 0x00000001
_WINDOWS_SHARE_WRITE = 0x00000002
_WINDOWS_SHARE_ALL = 0x00000007
_WINDOWS_OPEN_EXISTING = 3
_WINDOWS_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_WINDOWS_FILE_ATTRIBUTE_DIRECTORY = 0x00000010
_WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_WINDOWS_FILE_ATTRIBUTE_TAG_INFO = 9
_WINDOWS_INVALID_HANDLE = ctypes.c_void_p(-1).value


class GuardError(RuntimeError):
    pass


class GuardUnsupported(GuardError):
    pass


class GuardConflict(GuardError):
    pass


class GuardIO(GuardError):
    pass


class _WindowsAttributeTagInfo(ctypes.Structure):
    _fields_ = [("attributes", ctypes.c_uint32), ("reparse_tag", ctypes.c_uint32)]


def _windows_kernel32():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = ctypes.c_void_p
    kernel32.CreateFileW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    kernel32.GetFileInformationByHandleEx.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
    ]
    kernel32.GetFileInformationByHandleEx.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    return kernel32


def _windows_attributes(handle) -> _WindowsAttributeTagInfo:
    info = _WindowsAttributeTagInfo()
    ok = _windows_kernel32().GetFileInformationByHandleEx(
        ctypes.c_void_p(handle),
        _WINDOWS_FILE_ATTRIBUTE_TAG_INFO,
        ctypes.byref(info),
        ctypes.sizeof(info),
    )
    if not ok:
        raise OSError(ctypes.get_last_error(), "GetFileInformationByHandleEx")
    return info


def _windows_open_directory(path: Path):
    handle = _windows_kernel32().CreateFileW(
        str(path),
        _WINDOWS_FILE_LIST_DIRECTORY | _WINDOWS_FILE_READ_ATTRIBUTES | _WINDOWS_SYNCHRONIZE,
        _WINDOWS_SHARE_READ | _WINDOWS_SHARE_WRITE,
        None,
        _WINDOWS_OPEN_EXISTING,
        _WINDOWS_FILE_FLAG_BACKUP_SEMANTICS | _WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    if handle == _WINDOWS_INVALID_HANDLE:
        raise OSError(ctypes.get_last_error(), "CreateFileW directory")
    try:
        info = _windows_attributes(handle)
        if (
            not info.attributes & _WINDOWS_FILE_ATTRIBUTE_DIRECTORY
            or info.attributes & _WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise GuardConflict("GUARD_CONFLICT")
    except BaseException:
        _windows_kernel32().CloseHandle(ctypes.c_void_p(handle))
        raise
    return handle


def _windows_open_file(path: Path):
    handle = _windows_kernel32().CreateFileW(
        str(path),
        _WINDOWS_GENERIC_READ,
        _WINDOWS_SHARE_ALL,
        None,
        _WINDOWS_OPEN_EXISTING,
        _WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    if handle == _WINDOWS_INVALID_HANDLE:
        error = ctypes.get_last_error()
        if error in (2, 3):
            return None
        raise OSError(error, "CreateFileW file")
    try:
        info = _windows_attributes(handle)
        if (
            info.attributes & _WINDOWS_FILE_ATTRIBUTE_DIRECTORY
            or info.attributes & _WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise GuardConflict("GUARD_CONFLICT")
    except BaseException:
        _windows_kernel32().CloseHandle(ctypes.c_void_p(handle))
        raise
    return handle


class GuardedPath:
    """Open one target path relative to a verified, non-followed directory chain."""

    def __init__(self, root: Path, parts: tuple[str, ...]) -> None:
        if not parts:
            raise GuardConflict("GUARD_CONFLICT")
        self.root = root
        self.parts = parts
        self.created_dirs: list[Path] = []
        self._parent_fd: int | None = None
        self._parent: Path | None = None
        self._handles: list[int] = []

    def __enter__(self) -> "GuardedPath":
        if os.name == "nt":
            self._open_windows()
        elif _HAS_DIR_FD:
            self._open_posix()
        else:
            raise GuardUnsupported("GUARD_UNSUPPORTED")
        return self

    def __exit__(self, *_: object) -> None:
        if self._parent_fd is not None:
            os.close(self._parent_fd)
            self._parent_fd = None
        if self._handles:
            kernel32 = _windows_kernel32()
            for handle in self._handles:
                kernel32.CloseHandle(ctypes.c_void_p(handle))
            self._handles = []

    def _open_posix(self) -> None:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = None
        try:
            fd = os.open(self.root, flags)
            for index, part in enumerate(self.parts[:-1]):
                try:
                    child = os.open(part, flags, dir_fd=fd)
                except FileNotFoundError:
                    try:
                        os.mkdir(part, 0o700, dir_fd=fd)
                    except FileExistsError:
                        pass
                    else:
                        self.created_dirs.append(self.root.joinpath(*self.parts[: index + 1]))
                    child = os.open(part, flags, dir_fd=fd)
                os.close(fd)
                fd = child
            self._parent_fd = fd
        except GuardError:
            if fd is not None:
                os.close(fd)
            raise
        except OSError as exc:
            if fd is not None:
                os.close(fd)
            if exc.errno in {
                errno.ENOENT,
                errno.ELOOP,
                errno.ENOTDIR,
                errno.EACCES,
                errno.EPERM,
            }:
                raise GuardConflict("GUARD_CONFLICT") from None
            raise GuardIO("GUARD_IO") from None

    def _open_windows(self) -> None:
        try:
            self._handles.append(_windows_open_directory(self.root))
            current = self.root
            for index, part in enumerate(self.parts[:-1]):
                current = current / part
                if not os.path.lexists(current):
                    try:
                        os.mkdir(current)
                    except FileExistsError:
                        pass
                    self.created_dirs.append(self.root.joinpath(*self.parts[: index + 1]))
                self._handles.append(_windows_open_directory(current))
            self._parent = current
        except GuardError:
            self.__exit__()
            raise
        except OSError:
            self.__exit__()
            raise GuardConflict("GUARD_CONFLICT") from None

    @property
    def target(self) -> Path:
        return (self._parent or self.root.joinpath(*self.parts[:-1])) / self.parts[-1]

    def read_bytes(self, limit: int) -> bytes | None:
        if self._parent_fd is not None:
            return self._read_posix(limit)
        return self._read_windows(limit)

    def _read_posix(self, limit: int) -> bytes | None:
        name = self.parts[-1]
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(name, flags, dir_fd=self._parent_fd)
        except FileNotFoundError:
            return None
        except OSError:
            raise GuardConflict("GUARD_CONFLICT") from None
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
                raise GuardConflict("GUARD_CONFLICT")
            if info.st_size > limit:
                raise GuardConflict("GUARD_CONFLICT")
            data = os.read(fd, limit + 1)
            if len(data) > limit:
                raise GuardConflict("GUARD_CONFLICT")
            return data
        except GuardError:
            raise
        except OSError:
            raise GuardIO("GUARD_IO") from None
        finally:
            os.close(fd)

    def _read_windows(self, limit: int) -> bytes | None:
        import msvcrt

        handle = _windows_open_file(self.target)
        if handle is None:
            return None
        fd = None
        try:
            fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | getattr(os, "O_BINARY", 0))
            handle = None
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise GuardConflict("GUARD_CONFLICT")
            if info.st_size > limit:
                raise GuardConflict("GUARD_CONFLICT")
            data = os.read(fd, limit + 1)
            if len(data) > limit:
                raise GuardConflict("GUARD_CONFLICT")
            return data
        except GuardError:
            raise
        except OSError:
            raise GuardIO("GUARD_IO") from None
        finally:
            if fd is not None:
                os.close(fd)
            elif handle is not None:
                _windows_kernel32().CloseHandle(ctypes.c_void_p(handle))

    def write(self, content: str, mode: int) -> None:
        data = content.encode("utf-8")
        if self._parent_fd is not None:
            self._write_posix(data, mode)
        else:
            self._write_windows(data, mode)

    def _write_posix(self, data: bytes, mode: int) -> None:
        name = f".codepilot-apply-{uuid.uuid4().hex}"
        fd = None
        try:
            fd = os.open(
                name,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_BINARY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=self._parent_fd,
            )
            with os.fdopen(fd, "wb") as handle:
                fd = None
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
                os.fchmod(handle.fileno(), mode & 0o777)
            os.rename(
                name,
                self.parts[-1],
                src_dir_fd=self._parent_fd,
                dst_dir_fd=self._parent_fd,
            )
        except OSError:
            raise GuardIO("GUARD_IO") from None
        finally:
            if fd is not None:
                os.close(fd)
            try:
                os.unlink(name, dir_fd=self._parent_fd)
            except OSError:
                pass

    def _write_windows(self, data: bytes, mode: int) -> None:
        fd, name = tempfile.mkstemp(prefix=".codepilot-apply-", dir=self.target.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(name, mode & 0o777)
            os.replace(name, self.target)
        except OSError:
            raise GuardIO("GUARD_IO") from None
        finally:
            Path(name).unlink(missing_ok=True)

    def unlink(self) -> None:
        try:
            if self._parent_fd is not None:
                os.unlink(self.parts[-1], dir_fd=self._parent_fd)
            else:
                os.unlink(self.target)
        except FileNotFoundError:
            raise GuardConflict("GUARD_CONFLICT") from None
        except OSError:
            raise GuardIO("GUARD_IO") from None

    def read_text(self, limit: int) -> str | None:
        data = self.read_bytes(limit)
        if data is None:
            return None
        try:
            text = data.decode("utf-8-sig", errors="strict")
        except UnicodeError:
            raise GuardConflict("GUARD_CONFLICT") from None
        return text.replace("\r\n", "\n").replace("\r", "\n")

    def sha256(self, limit: int) -> str | None:
        text = self.read_text(limit)
        return None if text is None else hashlib.sha256(text.encode("utf-8")).hexdigest()
