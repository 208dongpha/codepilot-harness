import pytest

from codepilot.agent.state import AgentState, AgentStatus, InvalidTransition


def test_agent_state_tracks_steps_and_valid_transitions():
    state = AgentState()

    state.transition(AgentStatus.RUNNING)
    state.advance_step()
    state.transition(AgentStatus.WAITING_APPROVAL)
    state.transition(AgentStatus.RUNNING)
    state.transition(AgentStatus.VERIFYING)
    state.transition(AgentStatus.COMPLETED)

    assert state.status is AgentStatus.COMPLETED
    assert state.current_step == 1
    assert state.error is None


@pytest.mark.parametrize(
    ("start", "target"),
    [
        (AgentStatus.CREATED, AgentStatus.COMPLETED),
        (AgentStatus.RUNNING, AgentStatus.CREATED),
        (AgentStatus.WAITING_APPROVAL, AgentStatus.VERIFYING),
        (AgentStatus.VERIFYING, AgentStatus.WAITING_APPROVAL),
    ],
)
def test_invalid_transitions_are_rejected(start, target):
    state = AgentState(status=start)
    with pytest.raises(InvalidTransition):
        state.transition(target)


@pytest.mark.parametrize(
    "terminal", [AgentStatus.COMPLETED, AgentStatus.FAILED, AgentStatus.CANCELLED]
)
def test_terminal_states_cannot_transition(terminal):
    state = AgentState(status=terminal)
    with pytest.raises(InvalidTransition):
        state.transition(AgentStatus.RUNNING)


def test_failure_preserves_safe_error_code_and_cancel_stops_steps():
    failed = AgentState(status=AgentStatus.RUNNING)
    failed.fail("PROVIDER_ERROR")
    assert failed.status is AgentStatus.FAILED
    assert failed.error == "PROVIDER_ERROR"

    cancelled = AgentState(status=AgentStatus.WAITING_APPROVAL)
    cancelled.cancel()
    assert cancelled.status is AgentStatus.CANCELLED
    with pytest.raises(InvalidTransition):
        cancelled.advance_step()
