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


async def test_exact_count_over_limit_is_rejected():
    counter = AsyncMock(return_value=901)
    budget = TokenBudget(max_input_tokens=1000, reserved_output_tokens=100, counter=counter)
    with pytest.raises(TokenBudgetError, match="TOKEN_BUDGET_EXCEEDED"):
        await budget.check([{"role": "user", "content": "x" * 2200}], [])


async def test_failed_exact_count_keeps_conservative_label_and_rejects_oversize():
    counter = AsyncMock(side_effect=RuntimeError("provider unavailable"))
    budget = TokenBudget(max_input_tokens=1000, reserved_output_tokens=100, counter=counter)
    with pytest.raises(TokenBudgetError, match="TOKEN_COUNT_UNAVAILABLE_NEAR_LIMIT"):
        await budget.check([{"role": "user", "content": "x" * 4000}], [])


@pytest.mark.parametrize("adversarial", ["\u200d" * 700, "漢字" * 500, "\\u0000" * 500])
async def test_adversarial_unicode_fails_closed_near_limit_without_exact_count(adversarial):
    counter = AsyncMock(return_value=None)
    budget = TokenBudget(max_input_tokens=1000, reserved_output_tokens=100, counter=counter)
    with pytest.raises(TokenBudgetError, match="TOKEN_COUNT_UNAVAILABLE_NEAR_LIMIT"):
        await budget.check([{"role": "user", "content": adversarial}], [])


async def test_large_tool_schema_fails_closed_when_no_counter_is_configured():
    budget = TokenBudget(max_input_tokens=1000, reserved_output_tokens=100)
    tools = [{"name": "read", "description": "x" * 2200, "parameters": {}}]
    with pytest.raises(TokenBudgetError, match="TOKEN_COUNT_UNAVAILABLE_NEAR_LIMIT"):
        await budget.check([{"role": "user", "content": "task"}], tools)


def test_reserved_output_must_leave_input_capacity():
    with pytest.raises(ValueError):
        TokenBudget(max_input_tokens=100, reserved_output_tokens=100)
