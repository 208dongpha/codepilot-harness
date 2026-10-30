import pytest

from codepilot.permissions.engine import (
    Action,
    Decision,
    PermissionEngine,
    PermissionError,
)


def patch_action(content="new"):
    return Action(
        tool="apply_patch",
        operation="replace",
        target="src/app.py",
        arguments={"expected_sha256": "a" * 64, "content": content},
    )


def test_read_only_is_allowed_and_write_requires_approval():
    engine = PermissionEngine()
    read = Action(tool="read_file", operation="read", target="src/app.py", arguments={})
    assert engine.evaluate(read) is Decision.ALLOW
    assert engine.evaluate(patch_action()) is Decision.ASK
    assert engine.evaluate(
        Action(tool="run_command", operation="execute", target=".", arguments={})
    ) is Decision.DENY


def test_action_id_is_stable_for_canonical_arguments():
    first = Action(
        tool="apply_patch", operation="replace", target="a.py", arguments={"b": 2, "a": 1}
    )
    second = Action(
        tool="apply_patch", operation="replace", target="a.py", arguments={"a": 1, "b": 2}
    )
    assert first.action_id == second.action_id


def test_approval_is_exact_single_use_and_not_model_grantable():
    engine = PermissionEngine()
    action = patch_action()
    pending = engine.request(action)
    assert pending.action.action_id == action.action_id

    engine.approve(action.action_id)
    assert engine.consume(action) is True
    with pytest.raises(PermissionError, match="APPROVAL_ALREADY_USED"):
        engine.consume(action)

    changed = patch_action("different")
    with pytest.raises(PermissionError, match="APPROVAL_MISMATCH"):
        engine.consume(changed)

    # A fresh human decision may approve the same exact action again.
    engine.request(action)
    engine.approve(action.action_id)
    assert engine.consume(action) is True


def test_changed_pending_action_invalidates_prior_approval():
    engine = PermissionEngine()
    old = patch_action("old")
    engine.request(old)
    engine.approve(old.action_id)
    new = patch_action("new")
    engine.request(new)
    with pytest.raises(PermissionError, match="APPROVAL_MISMATCH"):
        engine.consume(old)


def test_denial_expiry_and_cancellation_never_authorize():
    now = [10.0]
    engine = PermissionEngine(ttl_seconds=5, clock=lambda: now[0])
    action = patch_action()
    engine.request(action)
    engine.deny(action.action_id)
    with pytest.raises(PermissionError, match="APPROVAL_DENIED"):
        engine.consume(action)

    engine.request(action)
    engine.approve(action.action_id)
    now[0] = 16.0
    with pytest.raises(PermissionError, match="APPROVAL_EXPIRED"):
        engine.consume(action)

    now[0] = 20.0
    engine.request(action)
    engine.cancel()
    with pytest.raises(PermissionError, match="APPROVAL_CANCELLED"):
        engine.consume(action)


def test_invalid_actions_are_rejected():
    with pytest.raises(ValueError):
        Action(tool="../../bad", operation="replace", target="a.py", arguments={})
    with pytest.raises(ValueError):
        Action(
            tool="apply_patch",
            operation="replace",
            target="a.py",
            arguments={"x": float("nan")},
        )
