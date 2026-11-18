"""Tests for the agent loop in src/book_agent/agent.py.

Each error case asserts on what the loop actually sent back to the model (FakeLLM's
recorded requests), not only on the final result.
"""

import json
from collections.abc import Sequence

import pytest

from book_agent.agent import (
    CALL_NAME,
    LAST_TURN_NOTICE,
    MAX_ITERATIONS,
    MAX_TURNS,
    REMINDER,
    REPAIR_NOTICE,
    AgentError,
    AgentFailure,
    AgentResult,
    run_agent,
)
from book_agent.domain.models import CatalogBook, Profile
from book_agent.domain.recommendations import Filters, Recommendation, Rule
from book_agent.domain.validation import WorkMatching
from book_agent.domain.works import SameWorkVerdict
from book_agent.llm.base import (
    LLMError,
    LLMResponse,
    Message,
    StopReason,
    TextPart,
    ToolCallPart,
    ToolResultPart,
    Usage,
)
from book_agent.ports import BookSearch, BookSearchError
from book_agent.prompts import agent_v2
from book_agent.same_work_judge import LLMSameWorkJudge
from tests.fakes import FakeLLM, FakeSameWorkJudge, text_response, tool_call_response
from tests.library_data import CLEAN_THREE, PROFILE, book, catalog, rec

SYSTEM = "You recommend books."
TOOLS = agent_v2.TOOLS
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
        tools=TOOLS,
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
            tools=TOOLS,
            llm=llm,
            books=catalog(),
            profile=kwargs.get("profile", PROFILE),
            filters=NO_FILTERS,
        )
    return error.value, llm


class FailingBookSearch:
    """BookSearch whose first `failures` calls fail, as on a timeout or a quota error.

    By default every call fails; the rest go to the shared test catalog.
    """

    def __init__(self, failures: int | None = None) -> None:
        self.failures = failures
        self.calls = 0
        self.catalog = catalog()

    async def search(
        self, query: str, *, language: str | None = None, max_results: int = 10
    ) -> list[CatalogBook]:
        self.calls += 1
        if self.failures is None or self.calls <= self.failures:
            raise BookSearchError("Google Books request timed out")
        return await self.catalog.search(query, language=language, max_results=max_results)

    async def find(self, title: str, author: str, *, max_results: int = 10) -> list[CatalogBook]:
        return await self.search(f"{title} {author}", max_results=max_results)

    async def get_details(self, volume_id: str) -> CatalogBook | None:
        return await self.catalog.get_details(volume_id)


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
    assert error.failure is AgentFailure.TOKEN_LIMIT
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
    # Only the search tool's call fails; validation's lookups work.
    result, llm = await run([tool_call_response(search()), submit()], FailingBookSearch(1))

    assert result.validation.fully_verified
    [error] = tool_results(llm.requests[1].messages[-1])
    assert error.tool_call_id == "search-1" and error.is_error
    assert error.content == "Book search failed: Google Books request timed out"


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


async def test_book_not_in_the_catalog_goes_back_to_the_model() -> None:
    # A book that doesn't exist (a likely hallucination) is a violation the model can fix.
    invented = rec("The Silent Library", "Nobody Real", "Quiet, like Dune.")
    result, llm = await run([submit([invented, *CLEAN_THREE[1:]]), submit(call_id="submit-2")])

    [feedback] = tool_results(llm.requests[1].messages[-1])
    assert feedback.is_error
    assert "- Book 1: 'The Silent Library' by Nobody Real: not found in the catalog\n" in (
        feedback.content
    )
    assert result.submissions == 2


async def test_failed_catalog_lookups_stop_the_run_without_feedback() -> None:
    # An outage, not a bad book: the model can't fix it, so no feedback round is spent.
    llm = FakeLLM([submit()])
    with pytest.raises(AgentError) as info:
        await run_agent(
            system=SYSTEM,
            user_prompt=USER_PROMPT,
            tools=TOOLS,
            llm=llm,
            books=FailingBookSearch(),
            profile=PROFILE,
            filters=NO_FILTERS,
        )
    error = info.value
    assert error.failure is AgentFailure.CATALOG_UNAVAILABLE
    assert str(error).startswith("the book catalog could not be reached (3 lookups failed")
    assert error.last_validation is not None
    assert [u.rule for u in error.last_validation.unknowns] == [Rule.NOT_FOUND] * 3
    assert error.rejections == []
    assert len(llm.requests) == 1


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
    assert error.failure is AgentFailure.ITERATIONS_EXHAUSTED
    assert error.last_validation is None and error.rejections == []
    assert len(llm.requests) == MAX_ITERATIONS == error.iterations == len(error.calls)


async def test_iterations_exhausted_after_repeated_rejections() -> None:
    bad = [DUNE_AGAIN, *CLEAN_THREE[1:]]
    script = [submit(bad, call_id=f"submit-{i}") for i in range(MAX_TURNS)]
    error, llm = await run_expecting_error(script)

    # The rejection on the last regular turn earned the repair turn; that one was
    # rejected too, so the run stops at the effective cap.
    assert str(error) == (
        f"no accepted recommendations after {MAX_TURNS} iterations including 1 repair turn "
        f"({MAX_TURNS} submissions rejected)"
    )
    assert error.failure is AgentFailure.ITERATIONS_EXHAUSTED
    assert error.repair_turn_used and error.iterations == MAX_TURNS
    assert len(error.rejections) == MAX_TURNS
    assert error.last_validation is error.rejections[-1]
    assert error.last_validation is not None
    assert [(v.rule, v.index) for v in error.last_validation.violations] == [(Rule.ALREADY_READ, 0)]
    assert len(llm.requests) == MAX_TURNS


async def test_llm_failure_raises_with_the_failed_call_recorded() -> None:
    error, llm = await run_expecting_error([LLMError("Gemini API error 503 UNAVAILABLE")])

    assert error.failure is AgentFailure.LLM_ERROR
    assert str(error) == "LLM call failed: Gemini API error 503 UNAVAILABLE"
    assert [(c.name, c.model, c.usage) for c in error.calls] == [(CALL_NAME, None, None)]
    assert error.iterations == 1 and len(llm.requests) == 1


# --- Trace -----------------------------------------------------------------------------


async def test_trace_records_each_turn_with_tool_outcomes() -> None:
    first = tool_call_response(
        search("s-1"), ToolCallPart(id="s-2", name="search_books", arguments={})
    )
    rejected = submit([DUNE_AGAIN, *CLEAN_THREE[1:]], call_id="submit-1")
    result, _ = await run(
        [text_response("Let me think."), first, rejected, submit(call_id="submit-2")]
    )

    turns = result.turns
    assert [t.iteration for t in turns] == [1, 2, 3, 4]
    assert (turns[0].note, turns[0].text, turns[0].tools) == (
        "no tool call: reminder sent",
        "Let me think.",
        [],
    )
    assert [(t.name, t.arguments, t.outcome, t.is_error) for t in turns[1].tools] == [
        ("search_books", {"query": "foundation"}, "1 book: Foundation", False),
        ("search_books", {}, "invalid arguments: query: Field required", True),
    ]
    assert turns[2].tools[0].outcome == "rejected: book 1 (already_read)"
    assert turns[3].tools[0].outcome == "accepted"
    assert turns[1].output_tokens == 5 and turns[1].stop_reason is StopReason.TOOL_USE


async def test_search_outcome_lists_titles_or_says_no_results() -> None:
    result, _ = await run(
        [tool_call_response(search("s-1", "hyperion"), search("s-2", "zzz")), submit()]
    )
    assert [t.outcome for t in result.turns[0].tools] == [
        "2 books: Hyperion; Hyperion",
        "no results",
    ]


async def test_failed_runs_carry_their_trace() -> None:
    script = [tool_call_response(search(f"search-{i}")) for i in range(MAX_ITERATIONS)]
    error, _ = await run_expecting_error(script)
    assert len(error.turns) == MAX_ITERATIONS
    assert all(t.tools[0].name == "search_books" for t in error.turns)


async def test_outage_turn_is_traced_before_stopping() -> None:
    llm = FakeLLM([submit()])
    with pytest.raises(AgentError) as info:
        await run_agent(
            system=SYSTEM,
            user_prompt=USER_PROMPT,
            tools=TOOLS,
            llm=llm,
            books=FailingBookSearch(),
            profile=PROFILE,
            filters=NO_FILTERS,
        )
    [turn] = info.value.turns
    assert turn.tools[0].outcome == "catalog unavailable: 3 lookups failed"
    assert turn.tools[0].is_error


# --- Last-turn notice ------------------------------------------------------------------


def text_parts(message: Message) -> list[str]:
    return [p.text for p in message.parts if isinstance(p, TextPart)]


async def test_last_turn_notice_follows_the_tool_results_of_the_next_to_last_turn() -> None:
    script = [tool_call_response(search(f"search-{i}")) for i in range(MAX_ITERATIONS - 1)]
    result, llm = await run([*script, submit()])

    assert result.iterations == MAX_ITERATIONS  # the model submitted on its last turn
    last_request = llm.requests[MAX_ITERATIONS - 1].messages
    closing = last_request[-1]
    assert closing.role == "user"
    assert isinstance(closing.parts[0], ToolResultPart)  # results first, notice last
    assert text_parts(closing) == [LAST_TURN_NOTICE]
    assert "submit_recommendations now" in LAST_TURN_NOTICE
    # No earlier turn got the notice.
    for request in llm.requests[: MAX_ITERATIONS - 1]:
        assert all(LAST_TURN_NOTICE not in text_parts(m) for m in request.messages)


async def test_last_turn_notice_joins_the_reminder_after_a_text_reply() -> None:
    script = [tool_call_response(search(f"search-{i}")) for i in range(MAX_ITERATIONS - 2)]
    _, llm = await run([*script, text_response("Thinking..."), submit()])

    closing = llm.requests[MAX_ITERATIONS - 1].messages[-1]
    assert closing.role == "user"
    assert text_parts(closing) == [REMINDER, LAST_TURN_NOTICE]  # one user message, not two


async def test_notice_is_traced_on_the_next_to_last_turn_only() -> None:
    script = [tool_call_response(search(f"search-{i}")) for i in range(MAX_ITERATIONS - 1)]
    result, _ = await run([*script, submit()])
    flags = [t.last_turn_notice for t in result.turns]
    assert flags == [False] * (MAX_ITERATIONS - 2) + [True, False]


async def test_runs_that_finish_early_never_get_the_notice() -> None:
    result, llm = await run([tool_call_response(search()), submit()])
    assert result.iterations == 2
    assert all(LAST_TURN_NOTICE not in text_parts(m) for r in llm.requests for m in r.messages)


# --- Repair turn (D-020) ------------------------------------------------------------------

BAD_LIST = [DUNE_AGAIN, *CLEAN_THREE[1:]]


def searches(count: int) -> list[LLMResponse]:
    return [tool_call_response(search(f"search-{i}")) for i in range(count)]


async def test_rejection_on_the_last_turn_gets_one_repair_turn() -> None:
    script = [*searches(MAX_ITERATIONS - 1), submit(BAD_LIST, "submit-1"), submit(call_id="s-2")]
    result, llm = await run(script)

    assert result.recommendations.recommendations == CLEAN_THREE
    assert result.repair_turn_used
    assert result.iterations == MAX_TURNS == len(llm.requests)
    closing = llm.requests[MAX_TURNS - 1].messages[-1]
    assert isinstance(closing.parts[0], ToolResultPart) and closing.parts[0].is_error
    assert closing.parts[0].content.startswith("Submission rejected.")
    assert text_parts(closing) == [REPAIR_NOTICE]  # after the feedback, same message
    assert result.turns[MAX_ITERATIONS - 1].repair_turn_granted


async def test_a_rejection_in_the_repair_turn_ends_the_run() -> None:
    script = [*searches(MAX_ITERATIONS - 1), submit(BAD_LIST, "s-1"), submit(BAD_LIST, "s-2")]
    error, llm = await run_expecting_error(script)

    assert error.failure is AgentFailure.ITERATIONS_EXHAUSTED
    assert error.repair_turn_used
    assert len(error.rejections) == 2 and len(llm.requests) == MAX_TURNS


async def test_searching_in_the_repair_turn_ends_the_run() -> None:
    script = [*searches(MAX_ITERATIONS - 1), submit(BAD_LIST), tool_call_response(search("x"))]
    error, llm = await run_expecting_error(script)
    assert error.repair_turn_used and len(llm.requests) == MAX_TURNS


async def test_an_earlier_rejection_never_adds_a_turn() -> None:
    # Rejected on the next-to-last turn: the last regular turn is left to fix it.
    script = [*searches(MAX_ITERATIONS - 2), submit(BAD_LIST), tool_call_response(search("x"))]
    error, llm = await run_expecting_error(script)

    assert not error.repair_turn_used
    assert len(llm.requests) == MAX_ITERATIONS
    assert all(not t.repair_turn_granted for t in error.turns)


async def test_no_repair_turn_when_the_last_turn_only_searches() -> None:
    error, llm = await run_expecting_error(searches(MAX_ITERATIONS))
    assert not error.repair_turn_used and len(llm.requests) == MAX_ITERATIONS


async def test_a_catalog_outage_on_the_last_turn_still_stops_at_once() -> None:
    llm = FakeLLM([*searches(MAX_ITERATIONS - 1), submit()])
    with pytest.raises(AgentError) as info:
        await run_agent(
            system=SYSTEM,
            user_prompt=USER_PROMPT,
            tools=TOOLS,
            llm=llm,
            books=FailingBookSearch(),
            profile=PROFILE,
            filters=NO_FILTERS,
        )
    assert info.value.failure is AgentFailure.CATALOG_UNAVAILABLE
    assert not info.value.repair_turn_used
    assert len(llm.requests) == MAX_ITERATIONS


async def test_accepted_runs_without_repair_say_so() -> None:
    result, _ = await run([submit()])
    assert not result.repair_turn_used
