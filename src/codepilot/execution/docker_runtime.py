"""Docker sandbox execution for smoke tests and configured verification commands.

No interface accepts arbitrary host commands and no failure path falls back to host execution.
"""

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from threading import Lock, Thread
from typing import IO
from uuid import uuid4

from codepilot.config import SandboxConfig

logger = logging.getLogger("codepilot")
IMAGE = "codepilot-sandbox:phase1"
SMOKE = "import os; assert os.getuid() != 0; print('codepilot-smoke-ok')"
WORKSPACE_MEMORY = "512m"
SMOKE_MEMORY = "256m"
_CORE_LIMIT = "core=0:0"
_READ_CHUNK = 8192


class DockerError(RuntimeError):
    pass


@dataclass
class SmokeResult:
    stdout: str
    stderr: str
    exit_code: int


@dataclass
class SandboxCommandResult:
    stdout: str
    stderr: str
    exit_code: int | None
    timed_out: bool
    truncated: bool


@dataclass
class _CapturedOutput:
    stdout: str
    stderr: str
    timed_out: bool
    truncated: bool


class _BoundedBuffer:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._data = bytearray()
        self._lock = Lock()
        self.truncated = False

    def append(self, chunk: bytes) -> None:
        with self._lock:
            remaining = self.limit - len(self._data)
            if remaining <= 0:
                self.truncated = True
                return
            if len(chunk) > remaining:
                self._data.extend(chunk[:remaining])
                self.truncated = True
            else:
                self._data.extend(chunk)

    def text(self) -> str:
        with self._lock:
            return bytes(self._data).decode("utf-8", errors="replace")


def _sandbox_user() -> str:
    getuid = getattr(os, "getuid", None)
    getgid = getattr(os, "getgid", None)
    if getuid is not None and getgid is not None:
        uid, gid = getuid(), getgid()
        if uid != 0:
            return f"{uid}:{gid}"
    return "10001:10001"


def _bound_text(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit], True


class DockerRuntime:
    def __init__(self, config: SandboxConfig) -> None:
        self.config = config

    def _call(self, args: list[str], timeout: int = 10) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                ["docker", *args], capture_output=True, text=True, timeout=timeout, check=False
            )
        except subprocess.TimeoutExpired:
            logger.error("docker_error unavailable_or_timeout")
            raise DockerError("Docker command timed out.") from None
        except OSError:
            logger.error("docker_error unavailable_or_timeout")
            raise DockerError("Docker unavailable.") from None

    def available(self) -> bool:
        try:
            return self._call(["info", "--format", "{{.ServerVersion}}"]).returncode == 0
        except DockerError:
            return False

    def _cleanup(self, name: str, timeout: int = 10) -> None:
        failure = None
        try:
            removed = self._call(["rm", "--force", name], timeout=timeout)
            if removed.returncode:
                failure = "removal command returned nonzero exit code"
        except DockerError as exc:
            failure = str(exc)

        # A failed inspect cannot distinguish absence from a daemon failure.
        # A successful exact-name listing can prove the container is absent.
        state = "final state NOT VERIFIED"
        try:
            remaining = self._call(
                [
                    "container",
                    "ls",
                    "--all",
                    "--filter",
                    f"name=^/{name}$",
                    "--format",
                    "{{.Names}}|{{.State}}",
                ],
                timeout=timeout,
            )
            if remaining.returncode == 0:
                state = "container still exists" if remaining.stdout.strip() else "absence verified"
            else:
                state = "final state NOT VERIFIED (query returned nonzero exit code)"
        except DockerError as exc:
            state = f"final state NOT VERIFIED ({exc})"
        if failure or state != "absence verified":
            detail = failure or "removal command returned success"
            raise DockerError(f"Container cleanup failed for {name}: {detail}; {state}.")

    def _drain(self, pipe: IO[bytes], buffer: _BoundedBuffer) -> None:
        try:
            while True:
                chunk = pipe.read(_READ_CHUNK)
                if not chunk:
                    break
                buffer.append(chunk)
        except (OSError, ValueError):
            pass
        finally:
            try:
                pipe.close()
            except (OSError, ValueError):
                pass

    def _start_bounded(
        self, name: str, timeout_seconds: int, max_output_chars: int
    ) -> _CapturedOutput:
        try:
            process = subprocess.Popen(
                ["docker", "start", "--attach", name],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError:
            logger.error("docker_error unavailable_or_timeout")
            raise DockerError("Docker unavailable.") from None

        stdout = _BoundedBuffer(max_output_chars * 4)
        stderr = _BoundedBuffer(max_output_chars * 4)
        readers = [
            Thread(target=self._drain, args=(process.stdout, stdout), daemon=True),
            Thread(target=self._drain, args=(process.stderr, stderr), daemon=True),
        ]
        for reader in readers:
            reader.start()

        timed_out = False
        try:
            process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            logger.error("docker_test TIMEOUT")
            try:
                self._call(["kill", name], timeout=10)
            except DockerError:
                pass
            try:
                process.kill()
            except OSError:
                pass
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
        finally:
            for reader in readers:
                reader.join(timeout=10)

        captured_stdout, stdout_truncated = _bound_text(stdout.text(), max_output_chars)
        captured_stderr, stderr_truncated = _bound_text(stderr.text(), max_output_chars)
        return _CapturedOutput(
            stdout=captured_stdout,
            stderr=captured_stderr,
            timed_out=timed_out,
            truncated=stdout.truncated or stderr.truncated or stdout_truncated or stderr_truncated,
        )

    def run_workspace(
        self,
        root: Path,
        command: list[str],
        *,
        timeout_seconds: int,
        max_output_chars: int,
    ) -> SandboxCommandResult:
        if (
            not command
            or len(command) > 64
            or any(
                not isinstance(part, str)
                or not part
                or len(part) > 1024
                or "\x00" in part
                for part in command
            )
        ):
            raise DockerError("Invalid sandbox command.")
        if timeout_seconds <= 0 or max_output_chars < 256:
            raise DockerError("Invalid sandbox limits.")
        try:
            root = root.absolute()
            root_text = str(root)
            if (
                any(ord(char) < 32 for char in root_text)
                or (os.name != "nt" and ":" in root_text)
                or root.is_symlink()
                or not root.is_dir()
            ):
                raise DockerError("Invalid sandbox workspace.")
        except OSError:
            raise DockerError("Invalid sandbox workspace.") from None
        if not self.available():
            raise DockerError("Docker daemon unavailable. No host fallback.")
        if self._call(["image", "inspect", IMAGE]).returncode != 0:
            raise DockerError(
                "Build image: docker build -f docker/Dockerfile.sandbox "
                "-t codepilot-sandbox:phase1 docker"
            )

        name = "codepilot-task-" + uuid4().hex
        execution_error = None
        try:
            created = self._call(
                [
                    "create",
                    "--name",
                    name,
                    "--network",
                    "none",
                    "--cpus",
                    "1",
                    "--memory",
                    WORKSPACE_MEMORY,
                    "--memory-swap",
                    WORKSPACE_MEMORY,
                    "--pids-limit",
                    "128",
                    "--ulimit",
                    _CORE_LIMIT,
                    "--user",
                    _sandbox_user(),
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges",
                    "--read-only",
                    "--tmpfs",
                    "/tmp:rw,nosuid,nodev,size=64m",
                    "--workdir",
                    "/workspace",
                    "--volume",
                    f"{root}:/workspace:rw",
                    "--env",
                    "HOME=/tmp",
                    "--env",
                    "TMPDIR=/tmp",
                    "--env",
                    "PYTHONDONTWRITEBYTECODE=1",
                    "--env",
                    "PYTHONUNBUFFERED=1",
                    IMAGE,
                    *command,
                ]
            )
            if created.returncode != 0:
                raise DockerError("Docker container creation failed.")
            output = self._start_bounded(name, timeout_seconds, max_output_chars)
            if output.timed_out:
                return SandboxCommandResult(
                    stdout=output.stdout,
                    stderr=output.stderr,
                    exit_code=None,
                    timed_out=True,
                    truncated=output.truncated,
                )
            state = self._call(["inspect", "--format", "{{.State.ExitCode}}", name])
            if state.returncode or not state.stdout.strip().isdigit():
                raise DockerError("Cannot inspect container exit code.")
            exit_code = int(state.stdout.strip())
            logger.info("docker_test PASS" if exit_code == 0 else "docker_test FAIL")
            return SandboxCommandResult(
                stdout=output.stdout,
                stderr=output.stderr,
                exit_code=exit_code,
                timed_out=False,
                truncated=output.truncated,
            )
        except DockerError as exc:
            execution_error = exc
            logger.error("docker_test FAIL")
            raise
        finally:
            try:
                self._cleanup(name, timeout=30)
            except DockerError as cleanup_error:
                logger.error("docker_cleanup FAIL")
                if execution_error is not None:
                    raise DockerError(f"{execution_error} {cleanup_error}") from None
                raise

    def smoke_test(self) -> SmokeResult:
        if not self.available():
            raise DockerError("Docker daemon unavailable. No host fallback.")
        if self._call(["image", "inspect", IMAGE]).returncode != 0:
            raise DockerError(
                "Build image: docker build -f docker/Dockerfile.sandbox "
                "-t codepilot-sandbox:phase1 docker"
            )
        name = "codepilot-smoke-" + uuid4().hex
        execution_error = None
        try:
            created = self._call(
                [
                    "create",
                    "--name",
                    name,
                    "--network",
                    "none",
                    "--cpus",
                    "1",
                    "--memory",
                    SMOKE_MEMORY,
                    "--memory-swap",
                    SMOKE_MEMORY,
                    "--pids-limit",
                    "64",
                    "--ulimit",
                    _CORE_LIMIT,
                    "--user",
                    "10001:10001",
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges",
                    "--read-only",
                    IMAGE,
                    "python",
                    "-c",
                    SMOKE,
                ]
            )
            if created.returncode != 0:
                raise DockerError("Docker container creation failed.")
            output = self._call(["start", "--attach", name], self.config.timeout_seconds)
            state = self._call(["inspect", "--format", "{{.State.ExitCode}}", name])
            if state.returncode or not state.stdout.strip().isdigit():
                raise DockerError("Cannot inspect container exit code.")
            result = SmokeResult(output.stdout, output.stderr, int(state.stdout.strip()))
            if (
                output.returncode
                or result.exit_code
                or result.stdout.strip() != "codepilot-smoke-ok"
            ):
                raise DockerError("Docker smoke test failed.")
        except DockerError as exc:
            execution_error = exc
            logger.error("docker_smoke FAIL")
            raise
        finally:
            try:
                self._cleanup(name)
            except DockerError as cleanup_error:
                logger.error("docker_cleanup FAIL")
                if execution_error is not None:
                    raise DockerError(f"{execution_error} {cleanup_error}") from None
                raise
        logger.info("docker_smoke PASS")
        return result
