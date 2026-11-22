"""One retry on transient provider errors, as a decorator around any LLMClient (D-030).

Provider adapters never retry (D-027): this wrapper is where the policy lives, and
wiring applies it for every caller (CLI, evals, the API later). Only LLMTransientError
is retried: server errors (HTTP 5xx) and network errors (timeouts, connection
failures), once, after a short pause with jitter. Rate limits keep their own handling
(the eval's PacedLLM); other errors are not transient.

A retried call is visible to callers: the response's `retries` is 1, or the final
error's `retries` is 1 when the retry failed too.
"""

import asyncio
import random
from collections.abc import Awaitable, Callable, Sequence

from book_agent.llm.base import (
    LLMClient,
    LLMResponse,
    LLMTransientError,
    Message,
    ToolChoice,
    ToolSpec,
)

RETRY_DELAY_S = 2.0  # a "high demand" 503 needs a moment, not milliseconds
RETRY_JITTER_S = 1.0  # plus up to this much, so parallel callers don't retry in step

Sleep = Callable[[float], Awaitable[None]]


def retry_jitter() -> float:
    return random.uniform(0, RETRY_JITTER_S)


class RetryingLLM:
    """LLMClient that resends a request once after a server or network error."""

    def __init__(
        self,
        inner: LLMClient,
        *,
        delay_s: float = RETRY_DELAY_S,
        jitter: Callable[[], float] = retry_jitter,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._inner = inner
        self._delay_s = delay_s
        self._jitter = jitter
        self._sleep = sleep

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
        async def send() -> LLMResponse:
            return await self._inner.complete(
                system=system,
                messages=messages,
                tools=tools,
                max_tokens=max_tokens,
                temperature=temperature,
                tool_choice=tool_choice,
            )

        try:
            return await send()
        except LLMTransientError:
            await self._sleep(self._delay_s + self._jitter())
        try:
            response = await send()
        except LLMTransientError as e:
            raise e.after_retry() from e
        return response.model_copy(update={"retries": response.retries + 1})
