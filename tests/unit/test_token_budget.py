from unittest.mock import AsyncMock

import pytest

from codepilot.context.token_budget import TokenBudget, TokenBudgetError


async def test_small_prompt_uses_conservative_estimate_without_remote_count():
    counter = AsyncMock(return_value=10)
    budget = TokenBudget(max_input_tokens=1000, reserved_output_tokens=100, counter=counter)
    decision = await budget.check([{"role": "user", "content": "hello"}], [])
    assert decision.allowed
    assert decision.exact is False
    counter.assert_not_awaited()


async def test_near_limit_uses_exact_count_when_available():
    counter = AsyncMock(return_value=700)
    budget = TokenBudget(max_input_tokens=1000, reserved_output_tokens=100, counter=counter)
    decision = await budget.check([{"role": "user", "content": "x" * 2200}], [])
    assert decision.allowed
    assert decision.tokens == 700
    assert decision.exact is True
    counter.assert_awaited_once()


async def test_failed_exact_count_keeps_conservative_label_and_rejects_oversize():
    counter = AsyncMock(side_effect=RuntimeError("provider unavailable"))
    budget = TokenBudget(max_input_tokens=1000, reserved_output_tokens=100, counter=counter)
    with pytest.raises(TokenBudgetError, match="TOKEN_BUDGET_EXCEEDED"):
        await budget.check([{"role": "user", "content": "x" * 4000}], [])


def test_reserved_output_must_leave_input_capacity():
    with pytest.raises(ValueError):
        TokenBudget(max_input_tokens=100, reserved_output_tokens=100)
