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
        except (OSError, subprocess.TimeoutExpired):
            logger.error("docker_error unavailable_or_timeout")
            raise DockerError("Docker unavailable or command timed out.") from None

    def available(self) -> bool:
        try:
            return self._call(["info", "--format", "{{.ServerVersion}}"]).returncode == 0
        except DockerError:
            return False

    def smoke_test(self) -> SmokeResult:
        if not self.available():
            raise DockerError("Docker daemon unavailable. No host fallback.")
        if self._call(["image", "inspect", IMAGE]).returncode != 0:
            raise DockerError(
                "Build image: docker build -f docker/Dockerfile.sandbox "
                "-t codepilot-sandbox:phase1 docker"
            )
        name = "codepilot-smoke-" + uuid4().hex
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
            logger.info("docker_smoke PASS")
            return result
        except DockerError:
            logger.error("docker_smoke FAIL")
            raise
        finally:
            cleanup = self._call(["rm", "--force", name])
            if cleanup.returncode:
                logger.error("docker_cleanup FAIL")
                raise DockerError("Container cleanup failed; inspect Docker containers.")
