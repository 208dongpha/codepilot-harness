import pytest

from codepilot.config import SandboxConfig
from codepilot.execution.docker_runtime import DockerRuntime


def test_real_docker_smoke(monkeypatch):
    runtime = DockerRuntime(SandboxConfig())
    if not runtime.available():
        pytest.skip("Docker daemon unavailable; real smoke test NOT VERIFIED")
    original_call = runtime._call
    observed = []

    def inspect_security(args, timeout=10):
        response = original_call(args, timeout)
        if args[0] == "create" and response.returncode == 0:
            name = args[args.index("--name") + 1]
            template = (
                "{{.Config.User}}|{{.HostConfig.NetworkMode}}|{{.HostConfig.NanoCpus}}|"
                "{{.HostConfig.Memory}}|{{.HostConfig.PidsLimit}}|"
                "{{.HostConfig.ReadonlyRootfs}}|{{.HostConfig.Privileged}}|"
                "{{len .Mounts}}|{{json .HostConfig.CapDrop}}|"
                "{{json .HostConfig.SecurityOpt}}"
            )
            inspection = original_call(["inspect", "--format", template, name])
            observed.append((inspection.returncode, inspection.stdout.strip()))
        return response

    monkeypatch.setattr(runtime, "_call", inspect_security)
    result = runtime.smoke_test()
    assert result.exit_code == 0
    assert result.stdout.strip() == "codepilot-smoke-ok"
    assert len(observed) == 1
    code, settings = observed[0]
    assert code == 0
    values = settings.split("|")
    assert values[:8] == [
        "10001:10001",
        "none",
        "1000000000",
        "268435456",
        "64",
        "true",
        "false",
        "0",
    ]
    assert '"ALL"' in values[8]
    assert "no-new-privileges" in values[9]
