"""In-memory fakes that implement the ports, for tests that should not touch the network."""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from book_agent.domain.models import CatalogBook
from book_agent.llm.base import (
    LLMResponse,
    Message,
    StopReason,
    TextPart,
    ToolCallPart,
    ToolSpec,
    Usage,
)


def _words(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.casefold()))


class FakeBookSearch:
    """BookSearch backed by a fixed list of books.

    A book matches when every word of the query appears in its title or authors (case
    and punctuation ignored), like a search engine. Records every call so tests can
    assert on what was asked.
    """

    def __init__(self, books: list[CatalogBook] | None = None) -> None:
        self.books = list(books or [])
        self.searches: list[tuple[str, str | None, int]] = []
        self.detail_lookups: list[str] = []

    async def search(
        self, query: str, *, language: str | None = None, max_results: int = 10
    ) -> list[CatalogBook]:
        self.searches.append((query, language, max_results))
        words = _words(query)
        matches = [
            b
            for b in self.books
            if words <= _words(" ".join([b.title, *b.authors]))
            and (language is None or b.language in (None, language))
        ]
        return matches[:max_results]

    async def get_details(self, volume_id: str) -> CatalogBook | None:
        self.detail_lookups.append(volume_id)
        return next((b for b in self.books if b.source_id == volume_id), None)


# --- LLM ----------------------------------------------------------------------


@dataclass(frozen=True)
class LLMRequest:
    """One recorded call to FakeLLM.complete."""

    system: str
    messages: tuple[Message, ...]
    tools: tuple[ToolSpec, ...]
    max_tokens: int
    temperature: float | None


class FakeLLM:
    """LLMClient that plays back a script of responses and records every request.

    Each complete() call returns the next scripted LLMResponse, or raises it if the
    scripted item is an exception (e.g. LLMRateLimitError). Calling past the end of the
    script fails the test, so a loop that calls the model too often fails loudly.
    """

    def __init__(self, script: Sequence[LLMResponse | Exception]) -> None:
        self._script = list(script)
        self.requests: list[LLMRequest] = []

    async def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
        max_tokens: int,
        temperature: float | None = None,
    ) -> LLMResponse:
        # Snapshot as tuples: the caller will keep appending to its own list.
        self.requests.append(
            LLMRequest(system, tuple(messages), tuple(tools), max_tokens, temperature)
        )
        if len(self.requests) > len(self._script):
            raise AssertionError(
                f"FakeLLM script exhausted: call {len(self.requests)} "
                f"but only {len(self._script)} scripted"
            )
        item = self._script[len(self.requests) - 1]
        if isinstance(item, Exception):
            raise item
        return item


def text_response(
    text: str,
    *,
    stop_reason: StopReason = StopReason.END_TURN,
    usage: Usage | None = None,
    model: str = "fake-model",
) -> LLMResponse:
    """An assistant reply with only text."""
    return LLMResponse(
        message=Message(role="assistant", parts=(TextPart(text=text),)),
        stop_reason=stop_reason,
        usage=usage or Usage(input_tokens=10, output_tokens=5),
        model=model,
    )


def tool_call_response(
    *calls: ToolCallPart,
    text: str | None = None,
    usage: Usage | None = None,
    model: str = "fake-model",
) -> LLMResponse:
    """An assistant reply that asks for one or more (parallel) tool calls."""
    parts: tuple[TextPart | ToolCallPart, ...] = ((TextPart(text=text),) if text else ()) + calls
    return LLMResponse(
        message=Message(role="assistant", parts=parts),
        stop_reason=StopReason.TOOL_USE,
        usage=usage or Usage(input_tokens=10, output_tokens=5),
        model=model,
    )
