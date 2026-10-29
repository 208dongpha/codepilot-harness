import os
import shutil
import subprocess
import time
from pathlib import Path

from pydantic import Field

from codepilot.tools.base import ReadOnlyTool, ToolArguments, ToolError
from codepilot.tools.workspace import Workspace


class SearchArgs(ToolArguments):
    query: str = Field(min_length=1, max_length=1024)
    path: str = Field(default=".", min_length=1, max_length=512)
    regex: bool = False
    limit: int = Field(default=100, ge=1, le=1000)


class SearchTool:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace

    def _executable(self) -> str:
        found = shutil.which("rg")
        if found is None:
            raise ToolError("RIPGREP_UNAVAILABLE")
        executable = Path(found).resolve()
        if executable.is_relative_to(self.workspace.root):
            raise ToolError("UNTRUSTED_RIPGREP")
        return str(executable)

    def _run(self, executable: str, text: str, args: SearchArgs, limit: int, timeout: float) -> str:
        if timeout <= 0:
            raise ToolError("SEARCH_TIMEOUT")
        command = [
            executable,
            "--no-config",
            "--engine=default",
            "--encoding=utf-8",
            "--line-number",
            "--no-heading",
            "--color=never",
            f"--max-count={limit}",
        ]
        if not args.regex:
            command.append("--fixed-strings")
        command.extend(["--regexp", args.query, "--", "-"])
        # Only bounded, policy-checked text goes to stdin. rg never opens repository paths.
        # Full matching lines (not per-match JSON) bound stdout by input + line prefixes.
        environment = {
            name: os.environ[name] for name in ("SYSTEMROOT", "WINDIR") if name in os.environ
        }
        try:
            result = subprocess.run(
                command,
                input=text,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=timeout,
                check=False,
                cwd=Path(executable).parent,
                env=environment,
            )
        except subprocess.TimeoutExpired:
            raise ToolError("SEARCH_TIMEOUT") from None
        except (OSError, UnicodeError):
            raise ToolError("SEARCH_ERROR") from None
        if result.returncode not in (0, 1):
            raise ToolError("INVALID_REGEX" if args.regex else "SEARCH_ERROR")
        return result.stdout if result.returncode == 0 else ""

    def search(self, args: SearchArgs) -> dict[str, object]:
        self.workspace.resolve(args.path, directory=True)
        executable = self._executable()
        deadline = time.monotonic() + self.workspace.config.timeout_seconds
        cap = min(args.limit, self.workspace.config.max_search_results)
        self._run(executable, "", args, cap, deadline - time.monotonic())
        listing = self.workspace.list_files(args.path)
        matches = []
        total_bytes = 0
        output_chars = 0
        skipped = 0
        truncated = listing.truncated
        for path in listing.paths:
            if time.monotonic() >= deadline:
                raise ToolError("SEARCH_TIMEOUT")
            try:
                text = self.workspace.read_text(path)
            except ToolError:
                skipped += 1
                continue
            total_bytes += len(text.encode("utf-8"))
            if total_bytes > self.workspace.config.max_total_bytes:
                truncated = True
                break
            output = self._run(
                executable, text, args, cap - len(matches), deadline - time.monotonic()
            )
            for line in output.splitlines():
                number, separator, content = line.partition(":")
                if not separator or not number.isdigit():
                    raise ToolError("SEARCH_ERROR")
                snippet = content[:512]
                cost = len(path) * 2 + len(snippet) * 2 + 100
                if output_chars + cost > self.workspace.config.max_output_chars // 2:
                    return {"matches": matches, "truncated": True, "skipped": skipped}
                matches.append({"path": path, "line": int(number), "content": snippet})
                output_chars += cost
                truncated |= len(content) > len(snippet)
                if len(matches) >= cap:
                    return {"matches": matches, "truncated": True, "skipped": skipped}
        return {"matches": matches, "truncated": truncated, "skipped": skipped}

    def tool(self) -> ReadOnlyTool:
        return ReadOnlyTool("search_code", SearchArgs, self.search)
