import ast
import time

from pydantic import BaseModel, Field

from codepilot.tools.base import ToolError
from codepilot.tools.workspace import Workspace


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
    syntax_errors: int = 0
    truncated: bool = False


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
            result.truncated = listing.truncated
        else:
            candidates = sorted(set(paths[: settings.max_files]))
            result.truncated = len(paths) > settings.max_files
        deadline = time.monotonic() + settings.timeout_seconds
        total_bytes = 0
        symbol_count = 0
        for path in candidates:
            if not path.endswith(".py"):
                continue
            if time.monotonic() >= deadline:
                result.truncated = True
                break
            try:
                text = self.workspace.read_text(path)
                normalized = (
                    self.workspace.resolve(path).relative_to(self.workspace.root).as_posix()
                )
            except ToolError:
                result.skipped += 1
                continue
            total_bytes += len(text.encode("utf-8"))
            if total_bytes > settings.max_total_bytes:
                result.truncated = True
                break
            try:
                tree = ast.parse(text)
            except (SyntaxError, ValueError, RecursionError):
                result.syntax_errors += 1
                continue
            entry = ModuleEntry(
                path=normalized, package=normalized.rsplit("/", 1)[-1] == "__init__.py"
            )
            pending = [(tree, "")]
            while pending:
                if time.monotonic() >= deadline:
                    result.truncated = True
                    break
                node, scope = pending.pop()
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    name = f"{scope}.{node.name}" if scope else node.name
                    if symbol_count >= settings.max_symbols:
                        result.truncated = True
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
                result.truncated = True
            if len(result.model_dump_json()) > budget:
                result.modules.pop()
                result.truncated = True
                break
            if symbol_count >= settings.max_symbols:
                result.truncated = True
                break
        return result
