import json
import subprocess
from unittest.mock import patch

import pytest

from codepilot.context.ast_worker import (
    AstWorkerConfig,
    AstWorkerError,
    BoundedAstParser,
)

VALID = json.dumps(
    {
        "results": [
            {
                "status": "ok",
                "symbols": [
                    {"name": "f", "kind": "function", "line": 1, "end_line": 2}
                ],
            }
        ],
        "truncated": False,
    }
).encode()


class FakeProcess:
    def __init__(self, stdout=VALID, returncode=0, timeout=False):
        self.stdout = stdout
        self.returncode = returncode
        self.timeout = timeout
        self.killed = False

    def communicate(self, input=None, timeout=None):
        if self.timeout:
            self.timeout = False
            raise subprocess.TimeoutExpired("ast-worker", 1)
        return self.stdout, b""

    def kill(self):
        self.killed = True


def parser() -> BoundedAstParser:
    return BoundedAstParser(
        AstWorkerConfig(timeout_seconds=10, memory_mb=256, cpu_seconds=5)
    )


def test_real_worker_parses_in_bounded_process():
    try:
        results = parser().parse(
            ["def f():\n    pass\n", "def broken(:\n", "class C:\n    def m(self):\n        pass\n"]
        )
    except AstWorkerError as exc:
        if str(exc) == "AST_LIMITS_UNAVAILABLE":
            pytest.skip("OS-level AST worker limits are unavailable in this environment.")
        raise
    assert [item.status for item in results] == ["ok", "syntax", "ok"]
    assert results[0].symbols[0].name == "f"
    assert results[2].symbols[0].name == "C"
    assert results[2].symbols[1].name == "C.m"


def test_worker_receives_minimal_environment_and_isolated_flags(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-secret")
    process = FakeProcess()
    with patch("codepilot.context.ast_worker.subprocess.Popen", return_value=process) as popen:
        parser().parse(["def f():\n    pass\n"])

    command = popen.call_args.args[0]
    assert command[1:3] == ["-I", "-B"]
    assert command[-2:] == [str(256 * 1024 * 1024), "5"]
    environment = popen.call_args.kwargs["env"]
    assert "GEMINI_API_KEY" not in environment


def test_worker_timeout_kills_process_and_fails_closed():
    process = FakeProcess(timeout=True)
    with patch("codepilot.context.ast_worker.subprocess.Popen", return_value=process):
        with pytest.raises(AstWorkerError, match="AST_TIMEOUT"):
            parser().parse(["def f():\n    pass\n"])
    assert process.killed


@pytest.mark.parametrize(
    "stdout,returncode,error",
    [
        (b"not-json", 0, "AST_WORKER_FAILED"),
        (json.dumps({"results": []}).encode(), 0, "AST_WORKER_FAILED"),
        (VALID, 3, "AST_LIMITS_UNAVAILABLE"),
        (VALID, 1, "AST_WORKER_FAILED"),
        (
            json.dumps(
                {"results": [{"status": "ok", "symbols": [{"name": "x"}]}]}
            ).encode(),
            0,
            "AST_WORKER_FAILED",
        ),
    ],
)
def test_invalid_worker_evidence_fails_closed(stdout, returncode, error):
    process = FakeProcess(stdout=stdout, returncode=returncode)
    with patch("codepilot.context.ast_worker.subprocess.Popen", return_value=process):
        with pytest.raises(AstWorkerError, match=error):
            parser().parse(["def f():\n    pass\n"])


def test_oversized_worker_input_is_rejected_before_spawn():
    with patch("codepilot.context.ast_worker.subprocess.Popen") as popen:
        with pytest.raises(AstWorkerError, match="AST_INPUT_LIMIT"):
            parser().parse(["x" * (8 * 1024 * 1024 + 1)])
    popen.assert_not_called()
