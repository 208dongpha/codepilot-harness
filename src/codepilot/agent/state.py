from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class AgentStatus(StrEnum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class InvalidTransition(RuntimeError):
    pass


TRANSITIONS = {
    AgentStatus.CREATED: frozenset({AgentStatus.RUNNING, AgentStatus.CANCELLED}),
    AgentStatus.RUNNING: frozenset(
        {
            AgentStatus.WAITING_APPROVAL,
            AgentStatus.VERIFYING,
            AgentStatus.COMPLETED,
            AgentStatus.FAILED,
            AgentStatus.CANCELLED,
        }
    ),
    AgentStatus.WAITING_APPROVAL: frozenset(
        {AgentStatus.RUNNING, AgentStatus.FAILED, AgentStatus.CANCELLED}
    ),
    AgentStatus.VERIFYING: frozenset(
        {AgentStatus.RUNNING, AgentStatus.COMPLETED, AgentStatus.FAILED, AgentStatus.CANCELLED}
    ),
    AgentStatus.COMPLETED: frozenset(),
    AgentStatus.FAILED: frozenset(),
    AgentStatus.CANCELLED: frozenset(),
}


class AgentState(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    status: AgentStatus = AgentStatus.CREATED
    current_step: int = Field(default=0, ge=0)
    error: str | None = Field(default=None, max_length=128)

    def transition(self, target: AgentStatus) -> None:
        if target not in TRANSITIONS[self.status]:
            raise InvalidTransition(f"Invalid transition: {self.status} -> {target}")
        self.status = target

    def advance_step(self) -> None:
        if self.status is not AgentStatus.RUNNING:
            raise InvalidTransition("Steps can only advance while running.")
        self.current_step += 1

    def fail(self, error: str) -> None:
        self.transition(AgentStatus.FAILED)
        self.error = error

    def cancel(self) -> None:
        self.transition(AgentStatus.CANCELLED)
