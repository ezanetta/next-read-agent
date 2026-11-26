"""In-memory fakes that implement the ports, for tests that should not touch the network."""

import asyncio
import re
from collections.abc import Sequence
from dataclasses import dataclass

from book_agent.domain.library import RelatedBook
from book_agent.domain.models import CatalogBook
from book_agent.domain.works import SameWorkAnswer, SameWorkVerdict, WorkInfo, WorkRef
from book_agent.llm.base import (
    LLMError,
    LLMResponse,
    Message,
    StopReason,
    TextPart,
    ToolCallPart,
    ToolChoice,
    ToolSpec,
    Usage,
)
from book_agent.ports import CoverLookupError, WorkLookupError


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
        self.finds: list[tuple[str, str]] = []

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

    async def find(self, title: str, author: str, *, max_results: int = 10) -> list[CatalogBook]:
        """Every word of the title and the author's last name must appear in the book."""
        self.finds.append((title, author))
        surname = author.split()[-1] if author.split() else author
        title_words, author_words = _words(title), _words(surname)
        matches = [
            b
            for b in self.books
            if title_words <= _words(b.title) and author_words <= _words(" ".join(b.authors))
        ]
        return matches[:max_results]

    async def get_details(self, volume_id: str) -> CatalogBook | None:
        self.detail_lookups.append(volume_id)
        return next((b for b in self.books if b.source_id == volume_id), None)


class FakeWorkResolver:
    """WorkResolver backed by an ISBN -> work key table. ISBNs in `failing` raise.

    ISBNs in neither are unknown (None). Records every lookup in order.
    """

    def __init__(
        self, works: dict[str, str] | None = None, failing: set[str] | None = None
    ) -> None:
        self.works = dict(works or {})
        self.failing = set(failing or set())
        self.lookups: list[str] = []

    async def work_for_isbn(self, isbn13: str) -> str | None:
        self.lookups.append(isbn13)
        if isbn13 in self.failing:
            raise WorkLookupError("Open Library returned HTTP 503")
        return self.works.get(isbn13)


class FakeWorkDescriptions:
    """WorkDescriptions backed by a work key -> WorkInfo table. Keys in `failing` raise."""

    def __init__(
        self, works: dict[str, WorkInfo] | None = None, failing: set[str] | None = None
    ) -> None:
        self.works = dict(works or {})
        self.failing = set(failing or set())
        self.lookups: list[str] = []

    async def work_info(self, work_key: str) -> WorkInfo | None:
        self.lookups.append(work_key)
        if work_key in self.failing:
            raise WorkLookupError("Open Library returned HTTP 503")
        return self.works.get(work_key)


class FakeEmbedder:
    """Embedder with deterministic bag-of-words vectors: texts that share words are
    similar, so retrieval tests read naturally. `fail` makes every call raise.

    Records each call's texts.
    """

    def __init__(self, *, dimensions: int = 64, model: str = "fake-embedding", fail: bool = False):
        self._dimensions = dimensions
        self._model = model
        self.fail = fail
        self.calls: list[list[str]] = []

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        if self.fail:
            raise LLMError("embedding failed")
        return [self._vector(t) for t in texts]

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self._dimensions
        for word in _words(text) - _STOPWORDS:
            vector[sum(map(ord, word)) % self._dimensions] += 1.0
        return vector


_STOPWORDS = {"title", "text", "task", "search", "result", "query", "by", "subjects", "a", "the"}


class FakeLibraryRetriever:
    """LibraryRetriever that returns fixed books, or raises LLMError when `fail` is set."""

    def __init__(self, books: list[RelatedBook] | None = None, *, fail: bool = False) -> None:
        self.books = list(books or [])
        self.fail = fail
        self.queries: list[str] = []

    async def related_books(self, query: str) -> list[RelatedBook]:
        self.queries.append(query)
        if self.fail:
            raise LLMError("embedding failed")
        return list(self.books)


class FakeSameWorkJudge:
    """SameWorkJudge that answers from a table keyed by the book's title.

    Books not in the table are DIFFERENT. Records every call, so tests can assert
    when the judge was (or was not) consulted and which candidates it saw.
    """

    def __init__(self, verdicts: dict[str, SameWorkVerdict] | None = None) -> None:
        self.verdicts = dict(verdicts or {})
        self.calls: list[tuple[WorkRef, tuple[WorkRef, ...]]] = []

    async def judge(self, book: WorkRef, candidates: Sequence[WorkRef]) -> SameWorkVerdict:
        self.calls.append((book, tuple(candidates)))
        return self.verdicts.get(book.title, SameWorkVerdict(answer=SameWorkAnswer.DIFFERENT))


# --- LLM ----------------------------------------------------------------------


@dataclass(frozen=True)
class LLMRequest:
    """One recorded call to FakeLLM.complete."""

    system: str
    messages: tuple[Message, ...]
    tools: tuple[ToolSpec, ...]
    max_tokens: int
    temperature: float | None
    tool_choice: ToolChoice | None = None


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
        tool_choice: ToolChoice | None = None,
    ) -> LLMResponse:
        # Snapshot as tuples: the caller will keep appending to its own list.
        self.requests.append(
            LLMRequest(system, tuple(messages), tuple(tools), max_tokens, temperature, tool_choice)
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


class FakeTime:
    """A monotonic clock that only moves when something sleeps: pacing tests run
    instantly and can still check the gaps between requests."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds
        await asyncio.sleep(0)


class FakeCoverFinder:
    """CoverFinder answering from a dict by title: a cover id, None (no cover), or a
    CoverLookupError to raise. Records each call with the fake time it was made at. With
    a `gate`, every lookup waits until the test sets it."""

    def __init__(
        self,
        answers: dict[str, int | CoverLookupError | None],
        time: FakeTime | None = None,
        *,
        gate: asyncio.Event | None = None,
    ) -> None:
        self.answers = answers
        self.time = time
        self.gate = gate
        self.calls: list[tuple[str, str]] = []
        self.call_times: list[float] = []

    async def cover_id(self, title: str, author: str) -> int | None:
        if self.gate is not None:
            await self.gate.wait()
        self.calls.append((title, author))
        if self.time is not None:
            self.call_times.append(self.time.monotonic())
        answer = self.answers.get(title)
        if isinstance(answer, CoverLookupError):
            raise answer
        return answer
