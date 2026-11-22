"""A record of one LLM call, for reporting tokens and latency per step of a run."""

from time import perf_counter
from typing import Self

from pydantic import BaseModel, Field

from book_agent.llm.base import (
    LLMError,
    LLMNetworkError,
    LLMResponse,
    LLMServerError,
    LLMTransientError,
    Usage,
)


class LLMCall(BaseModel, frozen=True):
    name: str  # the step, e.g. "interpret_request" or "recommend"
    model: str | None  # None when the call failed before a response
    usage: Usage | None
    latency_s: float
    retries: int = Field(default=0, ge=0)  # resent after a server error (D-030)
    error_status: int | None = None  # HTTP status of a failed call's server error
    network_error: bool = False  # a failed call that never got an answer

    @classmethod
    def succeeded(cls, name: str, response: LLMResponse, started: float) -> Self:
        """A call that got `response`; `started` is its perf_counter() start."""
        return cls(
            name=name,
            model=response.model,
            usage=response.usage,
            latency_s=perf_counter() - started,
            retries=response.retries,
        )

    @classmethod
    def failed(cls, name: str, error: LLMError, started: float) -> Self:
        return cls(
            name=name,
            model=None,
            usage=None,
            latency_s=perf_counter() - started,
            retries=error.retries if isinstance(error, LLMTransientError) else 0,
            error_status=error.status if isinstance(error, LLMServerError) else None,
            network_error=isinstance(error, LLMNetworkError),
        )

    @property
    def server_error(self) -> bool:
        return self.error_status is not None and 500 <= self.error_status <= 599

    @property
    def outage(self) -> bool:
        """Failed on the provider's side or on the way there, not because of the request."""
        return self.server_error or self.network_error


def total_usage(calls: list[LLMCall]) -> Usage | None:
    """Summed tokens over the calls that reported usage, or None if none did."""
    usages = [c.usage for c in calls if c.usage is not None]
    if not usages:
        return None
    return Usage(
        input_tokens=sum(u.input_tokens for u in usages),
        output_tokens=sum(u.output_tokens for u in usages),
    )
