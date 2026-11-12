import difflib
import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field

from codepilot.tools.base import ToolError
from codepilot.tools.workspace import Workspace
from codepilot.verification.models import DiffReport, FileChange

_MAX_CHANGES = 1000
_TRUNCATION_MARKER = "\n[DIFF_TRUNCATED]\n"
_TEXT_UNREADABLE = frozenset({"BINARY_FILE", "FILE_TOO_LARGE"})
_SENSITIVE = "SENSITIVE_CONTENT"


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _line_count(content: str) -> int:
    return len(content.splitlines())


@dataclass(frozen=True)
class WorkspaceSnapshot:
    """Trusted text baseline captured before an agent task starts."""

    contents: dict[str, str]
    complete: bool = True
    excluded: dict[str, str] = field(default_factory=dict)

    @classmethod
    def capture(cls, workspace: Workspace, max_total_bytes: int) -> "WorkspaceSnapshot":
        listing = workspace.list_files()
        contents: dict[str, str] = {}
        total = 0
        complete = not listing.truncated and not listing.linked_paths and listing.skipped == 0
        for path in listing.paths:
            try:
                text = workspace.read_text(path)
            except ToolError:
                complete = False
                continue
            total += len(text.encode("utf-8"))
            if total > max_total_bytes:
                complete = False
                break
            contents[path] = text
        return cls(
            contents=contents,
            complete=complete,
            excluded={entry.path: entry.signature for entry in listing.excluded},
        )


class GitDiffVerifier:
    """Build a git-style unified diff without executing repository code or git hooks."""

    def __init__(
        self,
        workspace: Workspace,
        max_total_bytes: int,
        max_diff_chars: int = 32000,
    ) -> None:
        if max_total_bytes < 1:
            raise ValueError("Snapshot limit must be positive.")
        if max_diff_chars < 256:
            raise ValueError("Diff limit must be at least 256 characters.")
        self.workspace = workspace
        self.max_total_bytes = max_total_bytes
        self.max_diff_chars = min(max_diff_chars, 60000)

    def compare(
        self,
        baseline: WorkspaceSnapshot,
        authorized: Mapping[str, str | None],
    ) -> DiffReport:
        if not baseline.complete:
            return DiffReport(incomplete=True)

        listing = self.workspace.list_files()
        current: dict[str, str] = {}
        unreadable: set[str] = set()
        unknown: set[str] = set()
        policy: set[str] = set()
        linked = set(listing.linked_paths)
        if linked:
            policy.add("LINKED_ENTRY_DETECTED")
            unreadable.update(linked)
        current_excluded = {entry.path: entry.signature for entry in listing.excluded}
        added_excluded = set(current_excluded) - set(baseline.excluded)
        removed_excluded = set(baseline.excluded) - set(current_excluded)
        modified_excluded = {
            path
            for path in set(current_excluded) & set(baseline.excluded)
            if current_excluded[path] != baseline.excluded[path]
        }
        excluded_changes = added_excluded | removed_excluded | modified_excluded
        if added_excluded:
            policy.add("EXCLUDED_ENTRY_ADDED")
        if removed_excluded:
            policy.add("EXCLUDED_ENTRY_REMOVED")
        if modified_excluded:
            policy.add("EXCLUDED_ENTRY_MODIFIED")
        total = 0
        size_exceeded = False
        for path in listing.paths:
            try:
                text = self.workspace.read_text(path)
            except ToolError as exc:
                if exc.code == _SENSITIVE:
                    policy.add("SENSITIVE_CONTENT_DETECTED")
                    unreadable.add(path)
                elif exc.code in _TEXT_UNREADABLE:
                    unreadable.add(path)
                else:
                    unknown.add(path)
                continue
            total += len(text.encode("utf-8"))
            if total > self.max_total_bytes:
                size_exceeded = True
                break
            current[path] = text

        enumeration_complete = (
            not listing.truncated
            and not unknown
            and not size_exceeded
            and not linked
            and listing.skipped == 0
        )
        changes: list[FileChange] = []
        diff_parts: list[str] = []
        used = 0
        truncated = False

        for path in sorted(
            set(baseline.contents) | set(current) | unreadable | excluded_changes
        ):
            if path in unknown:
                continue
            if path in excluded_changes:
                binary = True
                if path in added_excluded:
                    change = "added"
                elif path in removed_excluded:
                    change = "deleted"
                else:
                    change = "modified"
                authorized_change = False
                additions = deletions = 0
            else:
                binary = path in unreadable
                if binary:
                    change = "modified" if path in baseline.contents else "added"
                    authorized_change = False
                    additions = deletions = 0
                elif path not in baseline.contents:
                    change = "added"
                    authorized_change = path in authorized and authorized[path] == _sha256(
                        current[path]
                    )
                    additions = _line_count(current[path])
                    deletions = 0
                elif path not in current:
                    if not enumeration_complete:
                        continue
                    change = "deleted"
                    authorized_change = path in authorized and authorized[path] is None
                    additions = 0
                    deletions = _line_count(baseline.contents[path])
                else:
                    if baseline.contents[path] == current[path]:
                        continue
                    change = "modified"
                    authorized_change = path in authorized and authorized[path] == _sha256(
                        current[path]
                    )
                    additions = deletions = 0

            if not binary:
                old = baseline.contents.get(path, "")
                new = current.get(path, "")
                unified = list(
                    difflib.unified_diff(
                        old.splitlines(keepends=True),
                        new.splitlines(keepends=True),
                        fromfile=f"a/{path}",
                        tofile=f"b/{path}",
                    )
                )
                if change == "modified":
                    additions = sum(
                        1
                        for line in unified
                        if line.startswith("+") and not line.startswith("+++")
                    )
                    deletions = sum(
                        1
                        for line in unified
                        if line.startswith("-") and not line.startswith("---")
                    )
                file_diff = "".join(unified)
                remaining = self.max_diff_chars - used
                if remaining > len(_TRUNCATION_MARKER):
                    if len(file_diff) > remaining:
                        diff_parts.append(
                            file_diff[: remaining - len(_TRUNCATION_MARKER)]
                            + _TRUNCATION_MARKER
                        )
                        used = self.max_diff_chars
                        truncated = True
                    else:
                        diff_parts.append(file_diff)
                        used += len(file_diff)
                elif file_diff:
                    truncated = True

            changes.append(
                FileChange(
                    path=path,
                    change=change,
                    binary=binary,
                    authorized=authorized_change,
                    additions=additions,
                    deletions=deletions,
                )
            )

        changes.sort(key=lambda item: item.path)
        if len(changes) > _MAX_CHANGES:
            changes = changes[:_MAX_CHANGES]
            truncated = True
            enumeration_complete = False

        return DiffReport(
            changes=changes,
            diff="".join(diff_parts),
            truncated=truncated,
            incomplete=not enumeration_complete,
            policy_violations=sorted(policy),
        )
