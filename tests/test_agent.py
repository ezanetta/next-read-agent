"""Tests for the agent loop in src/book_agent/agent.py.

Each error case asserts on what the loop actually sent back to the model (FakeLLM's
recorded requests), not only on the final result.
"""

import json
from collections.abc import Sequence

import pytest

from book_agent.agent import CALL_NAME, MAX_ITERATIONS, TOOLS, AgentError, AgentResult, run_agent
from book_agent.domain.models import CatalogBook, Profile
from book_agent.domain.recommendations import Filters, Recommendation, Rule
from book_agent.domain.validation import WorkMatching
from book_agent.domain.works import SameWorkVerdict
from book_agent.llm.base import (
    LLMResponse,
    Message,
    StopReason,
    ToolCallPart,
    ToolResultPart,
    Usage,
)
from book_agent.ports import BookSearch, BookSearchError
from book_agent.same_work_judge import LLMSameWorkJudge
from tests.fakes import FakeLLM, FakeSameWorkJudge, text_response, tool_call_response
from tests.library_data import CLEAN_THREE, PROFILE, book, catalog, rec

SYSTEM = "You recommend books."
USER_PROMPT = "Recommend 3 books for me."
NO_FILTERS = Filters()
FOUNDATION_RESULT = {
    "title": "Foundation",
    "author": "Isaac Asimov",
    "pages": 255,
    "language": "en",
}

DUNE_AGAIN = rec("Dune", "Frank Herbert", "Like Ficciones.")  # already read
METAMORPHOSIS = rec("The Metamorphosis", "Franz Kafka", "A short classic, like Ficciones.")
SOLARIS = rec("Solaris", "Stanislaw Lem", "Strange, like Dune.")  # no page count in the catalog
KAFKA_READER = Profile(
    name="Reader", books=[*PROFILE.books, book("La metamorfosis", "Franz Kafka")]
)


def submit(
    recommendations: Sequence[Recommendation] = CLEAN_THREE, call_id: str = "submit-1"
) -> LLMResponse:
    """A submit_recommendations call; by default three books that pass every rule."""
    return tool_call_response(
        ToolCallPart(
            id=call_id,
            name="submit_recommendations",
            arguments={"recommendations": [r.model_dump() for r in recommendations]},
        )
    )


def search(call_id: str = "search-1", query: str = "foundation") -> ToolCallPart:
    return ToolCallPart(id=call_id, name="search_books", arguments={"query": query})


def tool_results(message: Message) -> list[ToolResultPart]:
    """The tool results in a user message, in order."""
    assert message.role == "user"
    return [p for p in message.parts if isinstance(p, ToolResultPart)]


async def run(
    script: Sequence[LLMResponse | Exception],
    books: BookSearch | None = None,
    *,
    profile: Profile = PROFILE,
    filters: Filters = NO_FILTERS,
    work_matching: WorkMatching | None = None,
) -> tuple[AgentResult, FakeLLM]:
    llm = FakeLLM(script)
    result = await run_agent(
        system=SYSTEM,
        user_prompt=USER_PROMPT,
        llm=llm,
        books=books or catalog(),
        profile=profile,
        filters=filters,
        work_matching=work_matching,
    )
    return result, llm


async def run_expecting_error(
    script: Sequence[LLMResponse | Exception], **kwargs: Profile
) -> tuple[AgentError, FakeLLM]:
    llm = FakeLLM(script)
    with pytest.raises(AgentError) as error:
        await run_agent(
            system=SYSTEM,
            user_prompt=USER_PROMPT,
            llm=llm,
            books=catalog(),
            profile=kwargs.get("profile", PROFILE),
            filters=NO_FILTERS,
        )
    return error.value, llm


class FailingBookSearch:
    """BookSearch whose every call fails, as on a timeout or a quota error."""

    async def search(
        self, query: str, *, language: str | None = None, max_results: int = 10
    ) -> list[CatalogBook]:
        raise BookSearchError("Google Books request timed out")

    async def get_details(self, volume_id: str) -> CatalogBook | None:
        raise BookSearchError("Google Books request timed out")


# --- Happy paths ---------------------------------------------------------------------


async def test_search_then_submit() -> None:
    first = tool_call_response(search())
    result, llm = await run([first, submit()])

    assert result.recommendations.recommendations == CLEAN_THREE
    assert result.validation.fully_verified
    assert result.validation.items[0].match is not None  # resolved book, with its link
    assert (result.iterations, result.submissions, result.rejections) == (2, 1, [])
    assert [c.name for c in result.calls] == [CALL_NAME, CALL_NAME]
    assert result.total_usage == Usage(input_tokens=20, output_tokens=10)

    assert llm.requests[0].system == SYSTEM
    assert llm.requests[0].tools == tuple(TOOLS)
    assert llm.requests[0].messages == (Message.user(USER_PROMPT),)
    history = llm.requests[1].messages
    assert history[1] == first.message  # the assistant turn is appended unchanged
    [result_part] = tool_results(history[2])
    assert result_part.tool_call_id == "search-1" and not result_part.is_error
    assert json.loads(result_part.content) == [FOUNDATION_RESULT]


async def test_text_reply_gets_a_reminder_then_submit() -> None:
    result, llm = await run([text_response("Here are three great books: ..."), submit()])

    assert len(result.recommendations.recommendations) == 3
    reminder = llm.requests[1].messages[-1]
    assert reminder.role == "user"
    assert "submit_recommendations" in reminder.text


async def test_max_tokens_without_tool_calls_raises_and_stops() -> None:
    error, llm = await run_expecting_error(
        [text_response("I think you would enjoy", stop_reason=StopReason.MAX_TOKENS)]
    )
    assert "token limit" in str(error)
    assert error.last_validation is None and error.iterations == 1
    assert len(llm.requests) == 1


# --- Errors go back to the model as is_error tool results --------------------------------


async def test_invalid_search_arguments_go_back_as_an_error() -> None:
    bad = tool_call_response(ToolCallPart(id="search-1", name="search_books", arguments={}))
    result, llm = await run([bad, submit()])

    assert len(result.recommendations.recommendations) == 3
    [error] = tool_results(llm.requests[1].messages[-1])
    assert error.tool_call_id == "search-1" and error.is_error
    assert error.content == "Invalid arguments for search_books: query: Field required"


async def test_invalid_submit_arguments_go_back_as_an_error() -> None:
    bad = tool_call_response(
        ToolCallPart(id="submit-1", name="submit_recommendations", arguments={"books": []})
    )
    result, llm = await run([bad, submit(call_id="submit-2")])

    assert result.submissions == 1  # a submission that doesn't parse is not validated
    [error] = tool_results(llm.requests[1].messages[-1])
    assert error.tool_call_id == "submit-1" and error.is_error
    assert error.content == (
        "Invalid arguments for submit_recommendations: recommendations: Field required"
    )


async def test_unknown_tool_lists_the_available_tools() -> None:
    bad = tool_call_response(ToolCallPart(id="moon-1", name="fly_to_moon", arguments={}))
    _, llm = await run([bad, submit()])

    [error] = tool_results(llm.requests[1].messages[-1])
    assert error.tool_call_id == "moon-1" and error.is_error
    assert error.content == (
        "Unknown tool 'fly_to_moon'. Available tools: search_books, submit_recommendations."
    )


async def test_book_search_failure_goes_back_as_an_error() -> None:
    result, llm = await run([tool_call_response(search()), submit()], FailingBookSearch())

    [error] = tool_results(llm.requests[1].messages[-1])
    assert error.tool_call_id == "search-1" and error.is_error
    assert error.content == "Book search failed: Google Books request timed out"
    # Validation could not look the books up either: NOT_FOUND unknowns, which the
    # acceptance policy accepts (only SAME_WORK unknowns block, D-014).
    assert [u.rule for u in result.validation.unknowns] == [Rule.NOT_FOUND] * 3


async def test_parallel_calls_each_get_a_result_in_order() -> None:
    parallel = tool_call_response(
        ToolCallPart(id="bad", name="search_books", arguments={}),
        search("good"),
    )
    _, llm = await run([parallel, submit()])

    results = tool_results(llm.requests[1].messages[-1])
    assert [r.tool_call_id for r in results] == ["bad", "good"]
    assert [r.is_error for r in results] == [True, False]
    assert json.loads(results[1].content) == [FOUNDATION_RESULT]


# --- Validation with feedback ------------------------------------------------------------


async def test_rejected_submission_gets_feedback_then_the_fix_is_accepted() -> None:
    first = submit([DUNE_AGAIN, *CLEAN_THREE[1:]], call_id="submit-1")
    result, llm = await run([first, submit(call_id="submit-2")])

    [feedback] = tool_results(llm.requests[1].messages[-1])
    assert feedback.tool_call_id == "submit-1" and feedback.is_error
    assert feedback.content == (
        "Submission rejected. Problems found:\n"
        "- Book 1: 'Dune' by Frank Herbert: already read (as 'Dune (Dune Chronicles, #1)')\n"
        'Accepted: 2 "The Left Hand of Darkness", 3 "Foundation". Keep them unchanged.\n'
        "Replace only book 1 with new books that meet every rule.\n"
        "Then call submit_recommendations again with exactly 3 books."
    )
    assert result.recommendations.recommendations == CLEAN_THREE
    assert (result.iterations, result.submissions) == (2, 2)
    assert [(v.rule, v.index) for v in result.rejections[0].violations] == [(Rule.ALREADY_READ, 0)]


async def test_same_work_unknown_is_rejected() -> None:
    # The judge failed for an author already in the library (D-012): not acceptable.
    judge = FakeSameWorkJudge({"The Metamorphosis": SameWorkVerdict(error="LLM call failed")})
    first = submit([METAMORPHOSIS, *CLEAN_THREE[1:]])
    result, llm = await run(
        [first, submit(call_id="submit-2")],
        profile=KAFKA_READER,
        work_matching=WorkMatching(judge=judge),
    )

    [feedback] = tool_results(llm.requests[1].messages[-1])
    assert feedback.is_error
    assert (
        "- Book 1: 'The Metamorphosis' by Franz Kafka: the author is in the library, but the "
        "same-work check failed (LLM call failed). It could not be verified as new to the "
        "reader, so replace it\n"
    ) in feedback.content
    assert result.rejections[0].violations == []  # rejected on the unknown alone
    assert result.submissions == 2


async def test_unknown_page_count_is_accepted() -> None:
    result, _ = await run([submit([SOLARIS, *CLEAN_THREE[1:]])], filters=Filters(max_pages=600))

    assert result.submissions == 1
    assert result.validation.passed and not result.validation.fully_verified
    assert [(u.rule, u.index) for u in result.validation.unknowns] == [(Rule.MAX_PAGES, 0)]


async def test_judge_calls_are_recorded_with_the_agent_calls() -> None:
    judge = LLMSameWorkJudge(
        FakeLLM([text_response('{"verdict": "different", "candidate": null}')])
    )
    result, _ = await run(
        [submit([METAMORPHOSIS, *CLEAN_THREE[1:]])],
        profile=KAFKA_READER,
        work_matching=WorkMatching(judge=judge),
    )
    assert [c.name for c in result.calls] == [CALL_NAME, "judge_same_work"]


# --- Limits --------------------------------------------------------------------------


async def test_max_iterations_raises_after_exactly_that_many_requests() -> None:
    # FakeLLM fails the test on a request beyond its script, so one more call would show.
    script = [tool_call_response(search(f"search-{i}")) for i in range(MAX_ITERATIONS)]
    error, llm = await run_expecting_error(script)

    assert str(error) == f"no accepted recommendations after {MAX_ITERATIONS} iterations"
    assert error.last_validation is None
    assert len(llm.requests) == MAX_ITERATIONS == error.iterations == len(error.calls)


async def test_iterations_exhausted_after_repeated_rejections() -> None:
    bad = [DUNE_AGAIN, *CLEAN_THREE[1:]]
    script = [submit(bad, call_id=f"submit-{i}") for i in range(MAX_ITERATIONS)]
    error, llm = await run_expecting_error(script)

    assert str(error) == (
        f"no accepted recommendations after {MAX_ITERATIONS} iterations "
        f"({MAX_ITERATIONS} submissions rejected)"
    )
    assert error.last_validation is not None
    assert [(v.rule, v.index) for v in error.last_validation.violations] == [(Rule.ALREADY_READ, 0)]
    assert len(llm.requests) == MAX_ITERATIONS
