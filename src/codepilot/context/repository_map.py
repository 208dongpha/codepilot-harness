import ast
import time
from typing import Literal

from pydantic import BaseModel, Field, computed_field

from codepilot.tools.base import ToolError
from codepilot.tools.workspace import Workspace

OmissionReason = Literal[
    "scan_limit",
    "read_error",
    "excluded_source",
    "file_size_limit",
    "syntax_error",
    "byte_limit",
    "map_budget",
    "symbol_limit",
    "deadline",
    "input_limit",
    "context_budget",
    "source_limit",
    "invalid_reference",
    "source_window",
    "input_incomplete",
]


class Symbol(BaseModel):
    name: str
    kind: str
    line: int
    end_line: int


class ModuleEntry(BaseModel):
    path: str = Field(min_length=1, max_length=512)
    package: bool
    symbols: list[Symbol] = Field(default_factory=list)


class RepositoryMap(BaseModel):
    modules: list[ModuleEntry] = Field(default_factory=list)
    skipped: int = 0
    scan_skipped: int = 0
    syntax_errors: int = 0
    truncated: bool = False
    reasons: list[OmissionReason] = Field(default_factory=list, max_length=15)

    @computed_field
    @property
    def incomplete(self) -> bool:
        return bool(
            self.truncated
            or self.skipped
            or self.scan_skipped
            or self.syntax_errors
            or self.reasons
        )

    def note(self, reason: OmissionReason) -> None:
        self.truncated = True
        if reason not in self.reasons:
            self.reasons.append(reason)


class RepositoryMapper:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace

    def build(self, paths: list[str] | None = None, max_chars: int | None = None) -> RepositoryMap:
        settings = self.workspace.config
        budget = min(max_chars or settings.max_map_chars, settings.max_map_chars)
        if budget < 128:
            raise ToolError("MAP_BUDGET_TOO_SMALL")
        result = RepositoryMap()
        if paths is None:
            listing = self.workspace.list_files()
            candidates = listing.paths
            result.scan_skipped = listing.skipped
            if listing.skipped:
                result.note("read_error")
            if listing.truncated:
                result.note("scan_limit")
        else:
            candidates = sorted(set(paths[: settings.max_files]))
            if len(paths) > settings.max_files:
                result.note("input_limit")
        deadline = time.monotonic() + settings.timeout_seconds
        total_bytes = 0
        symbol_count = 0
        for path in candidates:
            if not path.endswith(".py"):
                continue
            if time.monotonic() >= deadline:
                result.note("deadline")
                break
            try:
                text = self.workspace.read_text(path)
                normalized = (
                    self.workspace.resolve(path).relative_to(self.workspace.root).as_posix()
                )
            except ToolError as exc:
                result.skipped += 1
                reason = {
                    "PERMISSION_DENIED": "read_error",
                    "READ_ERROR": "read_error",
                    "NOT_FOUND": "read_error",
                    "FILE_TOO_LARGE": "file_size_limit",
                }
                result.note(reason.get(exc.code, "excluded_source"))
                continue
            total_bytes += len(text.encode("utf-8"))
            if total_bytes > settings.max_total_bytes:
                result.note("byte_limit")
                break
            try:
                tree = ast.parse(text)
            except (SyntaxError, ValueError, RecursionError):
                result.syntax_errors += 1
                result.note("syntax_error")
                continue
            entry = ModuleEntry(
                path=normalized, package=normalized.rsplit("/", 1)[-1] == "__init__.py"
            )
            pending = [(tree, "")]
            while pending:
                if time.monotonic() >= deadline:
                    result.note("deadline")
                    break
                node, scope = pending.pop()
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    name = f"{scope}.{node.name}" if scope else node.name
                    if symbol_count >= settings.max_symbols:
                        result.note("symbol_limit")
                        break
                    entry.symbols.append(
                        Symbol(
                            name=name,
                            kind="class" if isinstance(node, ast.ClassDef) else "function",
                            line=node.lineno,
                            end_line=node.end_lineno or node.lineno,
                        )
                    )
                    symbol_count += 1
                    scope = name
                pending.extend(
                    (child, scope) for child in reversed(list(ast.iter_child_nodes(node)))
                )
            result.modules.append(entry)
            while len(result.model_dump_json()) > budget and entry.symbols:
                entry.symbols.pop()
                result.note("map_budget")
            if len(result.model_dump_json()) > budget:
                result.modules.pop()
                result.note("map_budget")
                break
            if symbol_count >= settings.max_symbols:
                result.note("symbol_limit")
                break
        while len(result.model_dump_json()) > budget and result.modules:
            result.note("map_budget")
            result.modules.pop()
        if len(result.model_dump_json()) > budget:
            raise ToolError("MAP_BUDGET_TOO_SMALL")
        return result
