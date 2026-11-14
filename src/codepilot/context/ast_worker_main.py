"""Standalone AST worker executed in a bounded operating-system process.

The worker never imports project code, never executes parsed source, and reports only symbol
metadata. CPU, address-space/memory, and process-count limits are applied by the operating
system before any input is parsed.
"""

import ast
import ctypes
import json
import os
import sys

MAX_SYMBOLS = 20000


class _JobBasicLimits(ctypes.Structure):
    _fields_ = [
        ("per_process_user_time_limit", ctypes.c_int64),
        ("per_job_user_time_limit", ctypes.c_int64),
        ("limit_flags", ctypes.c_uint32),
        ("minimum_working_set_size", ctypes.c_size_t),
        ("maximum_working_set_size", ctypes.c_size_t),
        ("active_process_limit", ctypes.c_uint32),
        ("affinity", ctypes.c_size_t),
        ("priority_class", ctypes.c_uint32),
        ("scheduling_class", ctypes.c_uint32),
    ]


class _JobIoCounters(ctypes.Structure):
    _fields_ = [
        ("read_operations", ctypes.c_uint64),
        ("write_operations", ctypes.c_uint64),
        ("other_operations", ctypes.c_uint64),
        ("read_bytes", ctypes.c_uint64),
        ("write_bytes", ctypes.c_uint64),
        ("other_bytes", ctypes.c_uint64),
    ]


class _JobExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("basic", _JobBasicLimits),
        ("io", _JobIoCounters),
        ("process_memory_limit", ctypes.c_size_t),
        ("job_memory_limit", ctypes.c_size_t),
        ("peak_process_memory", ctypes.c_size_t),
        ("peak_job_memory", ctypes.c_size_t),
    ]


_JOB_LIMIT_PROCESS_MEMORY = 0x00000100
_JOB_LIMIT_JOB_TIME = 0x00000004
_JOB_LIMIT_ACTIVE_PROCESS = 0x00000008
_JOB_LIMIT_KILL_ON_CLOSE = 0x00002000
_JOB_EXTENDED_LIMIT_INFORMATION = 9


def _apply_posix_limits(memory_bytes: int, cpu_seconds: int) -> bool:
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        return True
    except (ImportError, OSError, ValueError):
        return False


def _apply_windows_limits(memory_bytes: int, cpu_seconds: int) -> bool:
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = ctypes.c_void_p
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return False
        limits = _JobExtendedLimits()
        limits.basic.limit_flags = (
            _JOB_LIMIT_PROCESS_MEMORY
            | _JOB_LIMIT_JOB_TIME
            | _JOB_LIMIT_ACTIVE_PROCESS
            | _JOB_LIMIT_KILL_ON_CLOSE
        )
        limits.basic.per_job_user_time_limit = cpu_seconds * 10_000_000
        limits.basic.active_process_limit = 1
        limits.process_memory_limit = memory_bytes
        configured = kernel32.SetInformationJobObject(
            ctypes.c_void_p(job),
            _JOB_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        )
        if not configured:
            return False
        assigned = kernel32.AssignProcessToJobObject(
            ctypes.c_void_p(job),
            ctypes.c_void_p(kernel32.GetCurrentProcess()),
        )
        return bool(assigned)
    except (AttributeError, OSError, ValueError):
        return False


def apply_limits(memory_bytes: int, cpu_seconds: int) -> bool:
    if os.name == "nt":
        return _apply_windows_limits(memory_bytes, cpu_seconds)
    return _apply_posix_limits(memory_bytes, cpu_seconds)


def collect_symbols(tree: ast.AST) -> tuple[list[dict[str, object]], bool]:
    symbols: list[dict[str, object]] = []
    pending: list[tuple[ast.AST, str]] = [(tree, "")]
    while pending:
        node, scope = pending.pop()
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            name = f"{scope}.{node.name}" if scope else node.name
            symbols.append(
                {
                    "name": name,
                    "kind": "class" if isinstance(node, ast.ClassDef) else "function",
                    "line": node.lineno,
                    "end_line": node.end_lineno or node.lineno,
                }
            )
            if len(symbols) >= MAX_SYMBOLS:
                return symbols, True
            scope = name
        pending.extend((child, scope) for child in reversed(list(ast.iter_child_nodes(node))))
    return symbols, False


def parse_texts(texts: list[str]) -> tuple[list[dict[str, object]], bool]:
    results: list[dict[str, object]] = []
    truncated = False
    for text in texts:
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError, RecursionError):
            results.append({"status": "syntax"})
            continue
        except MemoryError:
            results.append({"status": "resource"})
            continue
        except Exception:
            results.append({"status": "error"})
            continue
        symbols, cut = collect_symbols(tree)
        truncated = truncated or cut
        results.append({"status": "ok", "symbols": symbols})
    return results, truncated


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        return 2
    try:
        memory_bytes = int(argv[1])
        cpu_seconds = int(argv[2])
    except ValueError:
        return 2
    if memory_bytes <= 0 or cpu_seconds <= 0:
        return 2
    if not apply_limits(memory_bytes, cpu_seconds):
        sys.stderr.write("AST_LIMITS_UNAVAILABLE\n")
        return 3
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return 2
    texts = payload.get("texts") if isinstance(payload, dict) else None
    if not isinstance(texts, list) or any(not isinstance(text, str) for text in texts):
        return 2
    results, truncated = parse_texts(texts)
    json.dump({"results": results, "truncated": truncated}, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
