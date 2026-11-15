"""A record of one LLM call, for reporting tokens and latency per step of a run."""

from pydantic import BaseModel

from book_agent.llm.base import Usage


class LLMCall(BaseModel, frozen=True):
    name: str  # the step, e.g. "interpret_request" or "recommend"
    model: str | None  # None when the call failed before a response
    usage: Usage | None
    latency_s: float
