"""A recommendation as a stream of progress events, for the demo UI (D-043).

The pipeline runs as a task; its event sink (agent_events) turns what the loop already
records into UI events and queues them; this generator yields them as they arrive and
ends with `done` (the 3 books, cited library books and a trace summary) or `failed`.
Closing the generator early (the client went away) cancels the run.
"""

import asyncio
import contextlib
import re
import secrets
from collections.abc import AsyncGenerator, Collection
from time import perf_counter
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from book_agent.agent import AgentFailure, ToolTrace, TurnTrace
from book_agent.agent_events import (
    AgentEvent,
    RequestInterpreted,
    SubmissionChecked,
    SubmissionChecking,
    TurnFinished,
)
from book_agent.agent_run import AgentRun, run_agent_pipeline
from book_agent.demo_profiles import cover_url
from book_agent.domain.acceptance import blocking_problems
from book_agent.domain.models import ReadingStatus
from book_agent.domain.profiles import StoredProfile
from book_agent.domain.recommendations import (
    CheckedRecommendation,
    Filters,
    Unknown,
    ValidationResult,
)
from book_agent.domain.requests import FilterName
from book_agent.llm.base import LLMClient
from book_agent.ports import BookSearch, WorkResolver
from book_agent.prompts.agent_prompt import AgentPrompt
from book_agent.tools import SEARCH_BOOKS

MAX_REQUEST_CHARS = 500
Shelf = Literal["read", "to_read", "currently_reading"]
FailureKind = Literal["interpretation", "agent", "unavailable", "demo_limit"]

# --- Event payloads ---------------------------------------------------------------------


class ProblemView(BaseModel):
    """Why a submitted book was rejected."""

    book: int | None  # 1-based position in the submission; None for the whole submission
    rule: str
    message: str
    unknown: bool  # the check couldn't decide (e.g. the book wasn't found by the catalog)


class SearchStep(BaseModel):
    query: str
    outcome: str  # e.g. "5 books: Dune; Hyperion" or "no results"
    found: int  # how many books the search returned


class CitedLibraryBook(BaseModel):
    title: str
    author: str
    rating: float | None  # None when unrated, or picked (implied rating)
    picked: bool
    shelf: Shelf
    cover_url: str | None


class RecommendedBook(BaseModel):
    title: str
    author: str
    reason: str
    link: str | None
    cover_url: str | None
    pages: int | None  # of the matched edition, which the link points to
    language: str | None  # of the matched edition
    # No year: the catalog's is the edition's, not the original publication's (D-044).
    cited: list[CitedLibraryBook]


class TraceStep(BaseModel):
    iteration: int
    searches: list[SearchStep]
    submitted: list[str]
    outcome: str  # "searched", "accepted", "rejected", "no tool call", ...


class TraceSummary(BaseModel):
    prompt_id: str
    iterations: int
    submissions: int
    rejections: list[list[ProblemView]]
    steps: list[TraceStep]
    llm_calls: int
    input_tokens: int
    output_tokens: int
    duration_s: float


class RecommendationResult(BaseModel):
    profile_id: str
    request: str | None
    books: list[RecommendedBook]
    trace: TraceSummary


# --- Events -------------------------------------------------------------------------------


class StartedEvent(BaseModel):
    type: Literal["started"] = "started"
    run_id: str
    prompt_id: str


class InterpretedEvent(BaseModel):
    type: Literal["interpreted"] = "interpreted"
    preference: str | None
    filters: Filters
    sources: dict[str, str]  # filter name -> "text" or "flag"


class SearchingEvent(BaseModel):
    type: Literal["searching"] = "searching"
    iteration: int
    searches: list[SearchStep]


class SubmittedEvent(BaseModel):
    type: Literal["submitted"] = "submitted"
    iteration: int
    titles: list[str]


class CheckingEvent(BaseModel):
    """Existence, the reader's library and the filters are being checked."""

    type: Literal["checking"] = "checking"
    iteration: int


class RejectedEvent(BaseModel):
    """The submission was rejected; the agent tries again while it has turns left."""

    type: Literal["rejected"] = "rejected"
    iteration: int
    problems: list[ProblemView]


class DoneEvent(BaseModel):
    type: Literal["done"] = "done"
    result: RecommendationResult


class FailedEvent(BaseModel):
    type: Literal["failed"] = "failed"
    kind: FailureKind
    message: str


RecommendationEvent = Annotated[
    StartedEvent
    | InterpretedEvent
    | SearchingEvent
    | SubmittedEvent
    | CheckingEvent
    | RejectedEvent
    | DoneEvent
    | FailedEvent,
    Field(discriminator="type"),
]

# --- The stream ---------------------------------------------------------------------------


async def recommendation_events(
    stored: StoredProfile,
    request: str | None,
    flags: Filters,
    *,
    llm: LLMClient,
    books: BookSearch,
    works: WorkResolver | None,
    prompt: AgentPrompt,
    ignore_filters: Collection[FilterName] = (),
) -> AsyncGenerator[RecommendationEvent]:
    """Yield the run's events as they happen, ending with `done` or `failed`.
    `ignore_filters` drops interpreted filters the user removed (a rerun, D-045)."""
    queue: asyncio.Queue[RecommendationEvent] = asyncio.Queue()

    def sink(event: AgentEvent) -> None:
        for mapped in to_events(event):
            queue.put_nowait(mapped)

    started = perf_counter()
    yield StartedEvent(run_id=secrets.token_urlsafe(8), prompt_id=prompt.prompt_id)
    task = asyncio.create_task(
        run_agent_pipeline(
            stored.profile,
            flags,
            request=request,
            llm=llm,
            books=books,
            works=works,
            prompt=prompt,
            on_event=sink,
            ignore_filters=ignore_filters,
        )
    )
    try:
        while True:
            getter = asyncio.ensure_future(queue.get())
            done, _ = await asyncio.wait({getter, task}, return_when=asyncio.FIRST_COMPLETED)
            if getter in done:
                yield getter.result()
                continue
            getter.cancel()
            break
        while not queue.empty():
            yield queue.get_nowait()
        run = task.result()
        yield final_event(stored, run, perf_counter() - started)
    finally:
        if not task.done():  # the consumer left early: stop spending quota
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


def to_events(event: AgentEvent) -> list[RecommendationEvent]:
    match event:
        case RequestInterpreted(request=resolved):
            sources = {name: source.value for name, source in resolved.sources.items()}
            return [
                InterpretedEvent(
                    preference=resolved.preference, filters=resolved.filters, sources=sources
                )
            ]
        case SubmissionChecking(iteration=iteration, titles=titles):
            return [
                SubmittedEvent(iteration=iteration, titles=titles),
                CheckingEvent(iteration=iteration),
            ]
        case SubmissionChecked(iteration=iteration, validation=validation, accepted=accepted):
            if accepted:
                return []
            return [RejectedEvent(iteration=iteration, problems=problem_views(validation))]
        case TurnFinished(trace=trace):
            searches = _searches(trace)
            return (
                [SearchingEvent(iteration=trace.iteration, searches=searches)] if searches else []
            )


def problem_views(validation: ValidationResult) -> list[ProblemView]:
    return [
        ProblemView(
            book=p.index + 1 if p.index is not None else None,
            rule=p.rule.value,
            message=p.message,
            unknown=isinstance(p, Unknown),
        )
        for p in blocking_problems(validation)
    ]


def final_event(stored: StoredProfile, run: AgentRun, duration_s: float) -> RecommendationEvent:
    if run.error is not None or run.validation is None:
        return FailedEvent(kind=failure_kind(run), message=run.error or "no recommendation")
    return DoneEvent(result=recommendation_result(stored, run, duration_s))


def failure_kind(run: AgentRun) -> FailureKind:
    if run.request_error_kind is not None:
        return "interpretation"
    if run.failure in (AgentFailure.LLM_ERROR, AgentFailure.CATALOG_UNAVAILABLE):
        return "unavailable"
    return "agent"


def recommendation_result(
    stored: StoredProfile, run: AgentRun, duration_s: float
) -> RecommendationResult:
    assert run.validation is not None
    usage = run.total_usage
    return RecommendationResult(
        profile_id=stored.id,
        request=run.request_text,
        books=[recommended_book(stored, item) for item in run.validation.items],
        trace=TraceSummary(
            prompt_id=run.prompt_id,
            iterations=run.iterations,
            submissions=run.submissions,
            rejections=[problem_views(v) for v in run.rejections],
            steps=[trace_step(t) for t in run.turns],
            llm_calls=len(run.calls),
            input_tokens=usage.input_tokens if usage else 0,
            output_tokens=usage.output_tokens if usage else 0,
            duration_s=round(duration_s, 1),
        ),
    )


def recommended_book(stored: StoredProfile, item: CheckedRecommendation) -> RecommendedBook:
    """A checked recommendation as the UI shows it: the catalog edition's details when it
    was matched, and the library books its reason cites."""
    match = item.match
    cover = str(match.cover_url) if match and match.cover_url else None
    return RecommendedBook(
        title=match.title if match else item.recommendation.title,
        author=", ".join(match.authors) if match else item.recommendation.author,
        reason=item.recommendation.reason,
        link=str(match.link) if match else None,
        cover_url=cover,
        pages=match.pages if match else None,
        language=match.language if match else None,
        cited=[c for t in item.cited_titles if (c := cited_book(stored, t)) is not None],
    )


_SHELVES: dict[ReadingStatus, Shelf] = {
    ReadingStatus.READ: "read",
    ReadingStatus.TO_READ: "to_read",
    ReadingStatus.CURRENTLY_READING: "currently_reading",
}


def cited_book(stored: StoredProfile, title: str) -> CitedLibraryBook | None:
    """The library book a reason cites (cited titles are the profile's exact titles)."""
    for index, book in enumerate(stored.profile.books):
        if book.title == title:
            implied = stored.profile.ratings_implied
            return CitedLibraryBook(
                title=book.title,
                author=book.author,
                rating=None if implied else book.my_rating,
                picked=implied,
                shelf=_SHELVES[book.status],
                cover_url=cover_url(stored, index),
            )
    return None


_FOUND = re.compile(r"^(\d+) books?:")


def _searches(trace: TurnTrace) -> list[SearchStep]:
    return [
        SearchStep(query=str(t.arguments.get("query", "")), outcome=t.outcome, found=found(t))
        for t in trace.tools
        if t.name == SEARCH_BOOKS
    ]


def found(tool: ToolTrace) -> int:
    """Books a search returned, from its outcome ("5 books: ..."); 0 for "no results" and
    failed searches."""
    match = _FOUND.match(tool.outcome) if not tool.is_error else None
    return int(match.group(1)) if match else 0


def _submitted_titles(tool: ToolTrace) -> list[str]:
    items = tool.arguments.get("recommendations")
    if not isinstance(items, list):
        return []
    return [str(i.get("title", "")) for i in items if isinstance(i, dict)]


def trace_step(trace: TurnTrace) -> TraceStep:
    searches = _searches(trace)
    submits = [t for t in trace.tools if t.name != SEARCH_BOOKS]
    if submits:
        outcome = submits[-1].outcome.split(":")[0]  # "accepted", "rejected", ...
    elif searches:
        outcome = "searched"
    else:
        outcome = trace.note or "no tool call"
    return TraceStep(
        iteration=trace.iteration,
        searches=searches,
        submitted=[title for t in submits for title in _submitted_titles(t)],
        outcome=outcome,
    )
