import hashlib
import json
import re
import time
from collections.abc import Callable
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

READ_ONLY_TOOLS = frozenset({"list_files", "read_file", "search_code"})
WRITE_TOOLS = frozenset({"apply_patch"})
NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class Decision(StrEnum):
    ALLOW = "ALLOW"
    ASK = "ASK"
    DENY = "DENY"


class PermissionError(RuntimeError):
    pass


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True, frozen=True)

    tool: str = Field(min_length=1, max_length=64)
    operation: str = Field(min_length=1, max_length=64)
    target: str = Field(min_length=1, max_length=512)
    arguments: dict[str, Any]

    @model_validator(mode="after")
    def validate_canonical_form(self):
        if not NAME_PATTERN.fullmatch(self.tool) or not NAME_PATTERN.fullmatch(self.operation):
            raise ValueError("Invalid action name.")
        try:
            self.canonical()
        except (TypeError, ValueError) as exc:
            raise ValueError("Arguments must be finite JSON values.") from exc
        return self

    def canonical(self) -> str:
        return json.dumps(
            {
                "arguments": self.arguments,
                "operation": self.operation,
                "target": self.target,
                "tool": self.tool,
            },
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @computed_field
    @property
    def action_id(self) -> str:
        return hashlib.sha256(self.canonical().encode("utf-8")).hexdigest()


class PendingAction(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    action: Action
    action_id: str
    action_canonical: str = Field(exclude=True, repr=False)
    created_at: float
    expires_at: float
    approved: bool = False
    denied: bool = False
    cancelled: bool = False
    used: bool = False


class PermissionEngine:
    def __init__(
        self, ttl_seconds: float = 300, clock: Callable[[], float] = time.monotonic
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("Approval TTL must be positive.")
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._pending: PendingAction | None = None

    @property
    def pending(self) -> PendingAction | None:
        return self._pending.model_copy(deep=True) if self._pending is not None else None

    def evaluate(self, action: Action) -> Decision:
        if action.tool in READ_ONLY_TOOLS and action.operation == "read":
            return Decision.ALLOW
        if action.tool in WRITE_TOOLS and action.operation in {"create", "replace", "delete"}:
            return Decision.ASK
        return Decision.DENY

    def request(self, action: Action) -> PendingAction:
        if self.evaluate(action) is not Decision.ASK:
            raise PermissionError("ACTION_NOT_APPROVABLE")
        now = self._clock()
        self._pending = PendingAction(
            action=action.model_copy(deep=True),
            action_id=action.action_id,
            action_canonical=action.canonical(),
            created_at=now,
            expires_at=now + self.ttl_seconds,
        )
        return self._pending.model_copy(deep=True)

    def approve(self, action_id: str) -> None:
        pending = self._require_exact(action_id)
        self._ensure_active(pending)
        pending.approved = True

    def deny(self, action_id: str) -> None:
        pending = self._require_exact(action_id)
        self._ensure_active(pending)
        pending.denied = True

    def cancel(self) -> None:
        if self._pending is not None:
            self._pending.cancelled = True

    def consume(self, action: Action) -> bool:
        pending = self._pending
        try:
            matches = (
                pending is not None
                and pending.action_id == action.action_id
                and pending.action_canonical == action.canonical()
            )
        except (TypeError, ValueError):
            matches = False
        if not matches or pending is None:
            raise PermissionError("APPROVAL_MISMATCH")
        self._ensure_active(pending)
        if not pending.approved:
            raise PermissionError("APPROVAL_REQUIRED")
        pending.used = True
        return True

    def _require_exact(self, action_id: str) -> PendingAction:
        if self._pending is None or self._pending.action_id != action_id:
            raise PermissionError("APPROVAL_MISMATCH")
        return self._pending

    def _ensure_active(self, pending: PendingAction) -> None:
        if pending.used:
            raise PermissionError("APPROVAL_ALREADY_USED")
        if pending.cancelled:
            raise PermissionError("APPROVAL_CANCELLED")
        if pending.denied:
            raise PermissionError("APPROVAL_DENIED")
        if self._clock() > pending.expires_at:
            raise PermissionError("APPROVAL_EXPIRED")
