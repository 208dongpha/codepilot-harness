import json
from collections.abc import Awaitable, Callable

from pydantic import BaseModel

from codepilot.llm.base import Message


class TokenBudgetError(RuntimeError):
    pass


class TokenBudgetDecision(BaseModel):
    allowed: bool
    tokens: int
    input_limit: int
    exact: bool


class TokenBudget:
    """Bound model input with exact counting near the limit when available.

    The UTF-8-byte fallback is intentionally rough: model tokenization can vary for Unicode,
    escape-heavy text, source code, and model versions. It is never reported as exact. Near the
    configured limit, lack of an exact count therefore fails closed instead of trusting the
    heuristic.
    """

    def __init__(
        self,
        max_input_tokens: int,
        reserved_output_tokens: int,
        counter: Callable[[list[Message], list[dict[str, object]]], Awaitable[int | None]]
        | None = None,
    ) -> None:
        if max_input_tokens <= 0 or not 0 < reserved_output_tokens < max_input_tokens:
            raise ValueError("Token budget must reserve part of a positive input limit.")
        self.max_input_tokens = max_input_tokens
        self.reserved_output_tokens = reserved_output_tokens
        self.input_limit = max_input_tokens - reserved_output_tokens
        self.counter = counter

    async def check(
        self, messages: list[Message], tools: list[dict[str, object]]
    ) -> TokenBudgetDecision:
        serialized = json.dumps(
            {"messages": messages, "tools": tools},
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
        # Three UTF-8 bytes per token deliberately overestimates ordinary source text.
        estimate = max(1, (len(serialized.encode("utf-8")) + 2) // 3)
        tokens = estimate
        exact = False
        near_limit = estimate >= self.input_limit * 3 // 4
        if near_limit:
            counted = None
            if self.counter is not None:
                try:
                    counted = await self.counter(messages, tools)
                except Exception:
                    counted = None
            if counted is not None and counted >= 0:
                tokens = counted
                exact = True
            else:
                raise TokenBudgetError("TOKEN_COUNT_UNAVAILABLE_NEAR_LIMIT")
        if tokens > self.input_limit:
            raise TokenBudgetError("TOKEN_BUDGET_EXCEEDED")
        return TokenBudgetDecision(
            allowed=True, tokens=tokens, input_limit=self.input_limit, exact=exact
        )
