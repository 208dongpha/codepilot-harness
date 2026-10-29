"""Fixed Docker smoke test only; no host command execution interface."""

import logging
import subprocess
from dataclasses import dataclass
from uuid import uuid4

from codepilot.config import SandboxConfig

logger = logging.getLogger("codepilot")
IMAGE = "codepilot-sandbox:phase1"
SMOKE = "import os; assert os.getuid() != 0; print('codepilot-smoke-ok')"


class DockerError(RuntimeError):
    pass


@dataclass
class SmokeResult:
    stdout: str
    stderr: str
    exit_code: int


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

    def _cleanup(self, name: str) -> None:
        failure = None
        try:
            removed = self._call(["rm", "--force", name])
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
                ]
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
                    "256m",
                    "--pids-limit",
                    "64",
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
