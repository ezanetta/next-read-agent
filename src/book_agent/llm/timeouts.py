"""A deadline on every LLM request, as a decorator around any LLMClient (R-003).

The Gemini SDK sends requests with no timeout unless one is configured, so a stalled
connection would hold a recommendation (and its stream) open indefinitely. This wrapper
works for any provider: when the request takes longer than `timeout_s`, it's cancelled
and fails with LLMNetworkError, a transient error that RetryingLLM resends once.
"""

import asyncio
from collections.abc import Sequence

from book_agent.llm.base import (
    LLMClient,
    LLMNetworkError,
    LLMResponse,
    Message,
    ToolChoice,
    ToolSpec,
)

DEFAULT_LLM_TIMEOUT_S = 60.0  # a thinking model's longest normal turn stays well under it


class TimeoutLLM:
    """LLMClient whose requests fail with LLMNetworkError after `timeout_s` seconds."""

    def __init__(self, inner: LLMClient, *, timeout_s: float = DEFAULT_LLM_TIMEOUT_S) -> None:
        if timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        self._inner = inner
        self._timeout_s = timeout_s

    async def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
        max_tokens: int,
        temperature: float | None = None,
        tool_choice: ToolChoice | None = None,
    ) -> LLMResponse:
        try:
            async with asyncio.timeout(self._timeout_s):
                return await self._inner.complete(
                    system=system,
                    messages=messages,
                    tools=tools,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    tool_choice=tool_choice,
                )
        except TimeoutError as e:
            raise LLMNetworkError(f"LLM request timed out after {self._timeout_s:g} s") from e
