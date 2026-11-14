"""Bounded parent-side client for the standalone AST worker process."""

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

_WORKER = Path(__file__).with_name("ast_worker_main.py")
_MAX_PAYLOAD_BYTES = 8 * 1024 * 1024
_MAX_OUTPUT_BYTES = 8 * 1024 * 1024
_ENV_KEYS = ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATH")


class AstWorkerError(RuntimeError):
    pass


@dataclass(frozen=True)
class AstSymbol:
    name: str
    kind: Literal["class", "function"]
    line: int
    end_line: int


@dataclass(frozen=True)
class AstParseResult:
    status: Literal["ok", "syntax", "resource", "error"]
    symbols: tuple[AstSymbol, ...] = ()


@dataclass(frozen=True)
class AstWorkerConfig:
    timeout_seconds: float = 10
    memory_mb: int = 256
    cpu_seconds: int = 5


def _worker_env() -> dict[str, str]:
    return {key: os.environ[key] for key in _ENV_KEYS if key in os.environ}


def _valid_symbol(item: object) -> bool:
    if not isinstance(item, dict):
        return False
    name = item.get("name")
    kind = item.get("kind")
    line = item.get("line")
    end_line = item.get("end_line")
    return (
        isinstance(name, str)
        and 0 < len(name) <= 512
        and kind in ("class", "function")
        and isinstance(line, int)
        and isinstance(end_line, int)
        and not isinstance(line, bool)
        and not isinstance(end_line, bool)
        and 0 < line <= end_line <= 1000000
    )


class BoundedAstParser:
    def __init__(self, config: AstWorkerConfig) -> None:
        self.config = config

    def parse(self, texts: list[str], timeout_seconds: float | None = None) -> list[AstParseResult]:
        payload = json.dumps({"texts": texts}, ensure_ascii=False).encode("utf-8")
        if len(payload) > _MAX_PAYLOAD_BYTES:
            raise AstWorkerError("AST_INPUT_LIMIT")
        limit = min(timeout_seconds or self.config.timeout_seconds, self.config.timeout_seconds)
        try:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    str(_WORKER),
                    str(self.config.memory_mb * 1024 * 1024),
                    str(self.config.cpu_seconds),
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=_worker_env(),
            )
        except OSError:
            raise AstWorkerError("AST_UNAVAILABLE") from None
        try:
            stdout, _ = process.communicate(payload, timeout=limit)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise AstWorkerError("AST_TIMEOUT") from None
        if process.returncode == 3:
            raise AstWorkerError("AST_LIMITS_UNAVAILABLE")
        if process.returncode != 0 or len(stdout) > _MAX_OUTPUT_BYTES:
            raise AstWorkerError("AST_WORKER_FAILED")
        try:
            payload_out = json.loads(stdout)
        except ValueError:
            raise AstWorkerError("AST_WORKER_FAILED") from None
        results = payload_out.get("results") if isinstance(payload_out, dict) else None
        if not isinstance(results, list) or len(results) != len(texts):
            raise AstWorkerError("AST_WORKER_FAILED")
        parsed: list[AstParseResult] = []
        for item in results:
            status = item.get("status") if isinstance(item, dict) else None
            if status == "syntax":
                parsed.append(AstParseResult("syntax"))
            elif status == "resource":
                parsed.append(AstParseResult("resource"))
            elif status == "error":
                parsed.append(AstParseResult("error"))
            elif status == "ok" and isinstance(item.get("symbols"), list):
                raw_symbols = item["symbols"]
                if len(raw_symbols) > 20000 or not all(_valid_symbol(s) for s in raw_symbols):
                    raise AstWorkerError("AST_WORKER_FAILED")
                parsed.append(
                    AstParseResult(
                        "ok",
                        tuple(
                            AstSymbol(
                                name=raw["name"],
                                kind=raw["kind"],
                                line=raw["line"],
                                end_line=raw["end_line"],
                            )
                            for raw in raw_symbols
                        ),
                    )
                )
            else:
                raise AstWorkerError("AST_WORKER_FAILED")
        return parsed
