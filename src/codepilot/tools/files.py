from pydantic import Field

from codepilot.tools.base import ReadOnlyTool, ToolArguments
from codepilot.tools.workspace import Workspace


class ListFilesArgs(ToolArguments):
    path: str = Field(default=".", min_length=1, max_length=512)
    limit: int = Field(default=1000, ge=1, le=10000)


class ReadFileArgs(ToolArguments):
    path: str = Field(min_length=1, max_length=512)
    start_line: int = Field(default=1, ge=1, le=1000000)
    max_lines: int = Field(default=200, ge=1, le=2000)


class FileTools:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace

    def list_files(self, args: ListFilesArgs) -> dict[str, object]:
        listing = self.workspace.list_files(args.path, args.limit)
        paths = []
        size = 0
        truncated = listing.truncated
        for path in listing.paths:
            size += len(path) * 2 + 8
            if size > self.workspace.config.max_output_chars // 2:
                truncated = True
                break
            paths.append(path)
        return {"paths": paths, "truncated": truncated}

    def read_file(self, args: ReadFileArgs) -> dict[str, object]:
        text = self.workspace.read_text(args.path)
        path = self.workspace.resolve(args.path).relative_to(self.workspace.root).as_posix()
        lines = text.splitlines(keepends=True)
        selected = "".join(lines[args.start_line - 1 : args.start_line - 1 + args.max_lines])
        cap = self.workspace.config.max_output_chars // 4
        content = selected[:cap]
        return {
            "path": path,
            "start_line": args.start_line,
            "content": content,
            "truncated": len(selected) > cap or len(lines) >= args.start_line + args.max_lines,
        }

    def tools(self) -> tuple[ReadOnlyTool, ReadOnlyTool]:
        return (
            ReadOnlyTool("list_files", ListFilesArgs, self.list_files),
            ReadOnlyTool("read_file", ReadFileArgs, self.read_file),
        )
