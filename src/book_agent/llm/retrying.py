"""One retry on provider server errors, as a decorator around any LLMClient (D-030).

Provider adapters never retry (D-027): this wrapper is where the policy lives, and
wiring applies it for every caller (CLI, evals, the API later). Only LLMServerError
(HTTP 5xx) is retried, once, after a short pause with jitter. Rate limits keep their
own handling (the eval's PacedLLM); other errors are not transient.

A retried call is visible to callers: the response's `retries` is 1, or the final
LLMServerError's `retries` is 1 when the retry failed too.
"""

import asyncio
import random
from collections.abc import Awaitable, Callable, Sequence

from book_agent.llm.base import (
    LLMClient,
    LLMResponse,
    LLMServerError,
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
    """LLMClient that resends a request once when the provider answers with a 5xx."""

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
        except LLMServerError:
            await self._sleep(self._delay_s + self._jitter())
        try:
            response = await send()
        except LLMServerError as e:
            raise LLMServerError(
                f"{e} (after 1 retry)", status=e.status, retries=e.retries + 1
            ) from e
        return response.model_copy(update={"retries": response.retries + 1})
