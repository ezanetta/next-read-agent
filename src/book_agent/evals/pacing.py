"""Pacing and quota handling for eval runs: a decorator around any LLMClient.

- Requests are spaced to stay under a requests-per-minute limit.
- On a rate limit, wait the suggested retry_after once and retry.
- A daily quota, or a second rate limit in a row, raises QuotaExhausted.

QuotaExhausted is deliberately not an LLMError: the request parser, the agent and the
same-work judge turn LLMErrors into recorded failures, but none of them catch this, so
it ends the eval run instead of being saved as a failed run.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence

from book_agent.llm.base import LLMClient, LLMRateLimitError, LLMResponse, Message, ToolSpec

DEFAULT_MAX_RPM = 15  # gemini-3.5-flash-lite free tier
DEFAULT_RETRY_WAIT_S = 60.0  # when a rate limit suggests no retry_after

Clock = Callable[[], float]
Sleep = Callable[[float], Awaitable[None]]


class QuotaExhausted(Exception):
    """The provider's quota is used up for now: stop the eval run, keep what is done."""


class PacedLLM:
    """LLMClient that paces requests and handles rate limits for batch eval runs."""

    def __init__(
        self,
        inner: LLMClient,
        *,
        max_rpm: int = DEFAULT_MAX_RPM,
        clock: Clock = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if max_rpm < 1:
            raise ValueError("max_rpm must be at least 1")
        self._inner = inner
        self._interval = 60.0 / max_rpm
        self._clock = clock
        self._sleep = sleep
        self._last_start: float | None = None
        self.requests = 0  # requests sent, retries included

    async def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
        max_tokens: int,
        temperature: float | None = None,
    ) -> LLMResponse:
        async def send() -> LLMResponse:
            await self._wait_for_slot()
            return await self._inner.complete(
                system=system,
                messages=messages,
                tools=tools,
                max_tokens=max_tokens,
                temperature=temperature,
            )

        try:
            return await send()
        except LLMRateLimitError as e:
            if e.daily:
                raise QuotaExhausted(f"daily quota exhausted: {e}") from e
            await self._sleep(e.retry_after if e.retry_after is not None else DEFAULT_RETRY_WAIT_S)
        try:
            return await send()
        except LLMRateLimitError as e:
            raise QuotaExhausted(f"rate limited twice in a row: {e}") from e

    async def _wait_for_slot(self) -> None:
        """Keep at least 60 / max_rpm seconds between request starts."""
        now = self._clock()
        if self._last_start is not None:
            wait = self._last_start + self._interval - now
            if wait > 0:
                await self._sleep(wait)
                now = self._clock()
        self._last_start = now
        self.requests += 1
