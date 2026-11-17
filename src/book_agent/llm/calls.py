"""A record of one LLM call, for reporting tokens and latency per step of a run."""

from pydantic import BaseModel

from book_agent.llm.base import Usage


class LLMCall(BaseModel, frozen=True):
    name: str  # the step, e.g. "interpret_request" or "recommend"
    model: str | None  # None when the call failed before a response
    usage: Usage | None
    latency_s: float


def total_usage(calls: list[LLMCall]) -> Usage | None:
    """Summed tokens over the calls that reported usage, or None if none did."""
    usages = [c.usage for c in calls if c.usage is not None]
    if not usages:
        return None
    return Usage(
        input_tokens=sum(u.input_tokens for u in usages),
        output_tokens=sum(u.output_tokens for u in usages),
    )
