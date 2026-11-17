"""Tests for the agent loop in src/book_agent/agent.py.

Each error case asserts on what the loop actually sent back to the model (FakeLLM's
recorded requests), not only on the final result.
"""

import json
from collections.abc import Sequence

import pytest

from book_agent.agent import MAX_ITERATIONS, TOOLS, AgentError, run_agent
from book_agent.domain.models import CatalogBook
from book_agent.domain.recommendations import RecommendationList
from book_agent.llm.base import LLMResponse, Message, StopReason, ToolCallPart, ToolResultPart
from book_agent.ports import BookSearch, BookSearchError
from tests.fakes import FakeBookSearch, FakeLLM, text_response, tool_call_response
from tests.library_data import CLEAN_THREE, catalog_book

SYSTEM = "You recommend books."
USER_PROMPT = "Recommend 3 books for me."
DUNE = catalog_book("dune", "Dune", "Frank Herbert", 412)


def submit(call_id: str = "submit-1") -> LLMResponse:
    """A submit_recommendations call with three valid books."""
    return tool_call_response(
        ToolCallPart(
            id=call_id,
            name="submit_recommendations",
            arguments={"recommendations": [r.model_dump() for r in CLEAN_THREE]},
        )
    )


def search(call_id: str = "search-1", query: str = "dune") -> ToolCallPart:
    return ToolCallPart(id=call_id, name="search_books", arguments={"query": query})


def tool_results(message: Message) -> list[ToolResultPart]:
    """The tool results in a user message, in order."""
    assert message.role == "user"
    return [p for p in message.parts if isinstance(p, ToolResultPart)]


async def run(
    script: Sequence[LLMResponse | Exception], books: BookSearch | None = None
) -> tuple[RecommendationList, FakeLLM]:
    llm = FakeLLM(script)
    result = await run_agent(
        system=SYSTEM, user_prompt=USER_PROMPT, llm=llm, books=books or FakeBookSearch([DUNE])
    )
    return result, llm


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

    assert result == RecommendationList(recommendations=CLEAN_THREE)
    assert len(llm.requests) == 2
    assert llm.requests[0].system == SYSTEM
    assert llm.requests[0].tools == tuple(TOOLS)
    assert llm.requests[0].messages == (Message.user(USER_PROMPT),)

    history = llm.requests[1].messages
    assert history[1] == first.message  # the assistant turn is appended unchanged
    [result_part] = tool_results(history[2])
    assert result_part.tool_call_id == "search-1" and not result_part.is_error
    assert json.loads(result_part.content) == [
        {"title": "Dune", "author": "Frank Herbert", "pages": 412, "language": "en"}
    ]


async def test_text_reply_gets_a_reminder_then_submit() -> None:
    result, llm = await run([text_response("Here are three great books: ..."), submit()])

    assert len(result.recommendations) == 3
    reminder = llm.requests[1].messages[-1]
    assert reminder.role == "user"
    assert "submit_recommendations" in reminder.text


async def test_max_tokens_without_tool_calls_raises_and_stops() -> None:
    llm = FakeLLM([text_response("I think you would enjoy", stop_reason=StopReason.MAX_TOKENS)])
    with pytest.raises(AgentError, match="token limit"):
        await run_agent(system=SYSTEM, user_prompt=USER_PROMPT, llm=llm, books=FakeBookSearch())
    assert len(llm.requests) == 1


# --- Errors go back to the model as is_error tool results --------------------------------


async def test_invalid_search_arguments_go_back_as_an_error() -> None:
    bad = tool_call_response(ToolCallPart(id="search-1", name="search_books", arguments={}))
    books = FakeBookSearch([DUNE])
    result, llm = await run([bad, submit()], books)

    assert len(result.recommendations) == 3
    [error] = tool_results(llm.requests[1].messages[-1])
    assert error.tool_call_id == "search-1" and error.is_error
    assert "Invalid arguments for search_books" in error.content
    assert "query" in error.content  # names the missing field
    assert books.searches == []  # the search never ran


async def test_invalid_submit_arguments_go_back_as_an_error() -> None:
    bad = tool_call_response(
        ToolCallPart(id="submit-1", name="submit_recommendations", arguments={"books": []})
    )
    result, llm = await run([bad, submit("submit-2")])

    assert len(result.recommendations) == 3
    [error] = tool_results(llm.requests[1].messages[-1])
    assert error.tool_call_id == "submit-1" and error.is_error
    assert "Invalid arguments for submit_recommendations" in error.content
    assert "recommendations" in error.content


async def test_unknown_tool_lists_the_available_tools() -> None:
    bad = tool_call_response(ToolCallPart(id="moon-1", name="fly_to_moon", arguments={}))
    _, llm = await run([bad, submit()])

    [error] = tool_results(llm.requests[1].messages[-1])
    assert error.tool_call_id == "moon-1" and error.is_error
    assert "fly_to_moon" in error.content
    assert "search_books, submit_recommendations" in error.content


async def test_book_search_failure_goes_back_as_an_error() -> None:
    result, llm = await run([tool_call_response(search()), submit()], FailingBookSearch())

    assert len(result.recommendations) == 3
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
    assert json.loads(results[1].content)[0]["title"] == "Dune"


# --- Limits --------------------------------------------------------------------------


async def test_max_iterations_raises_after_exactly_that_many_requests() -> None:
    # FakeLLM fails the test on a request beyond its script, so one more call would show.
    script = [tool_call_response(search(f"search-{i}")) for i in range(MAX_ITERATIONS)]
    llm = FakeLLM(script)
    with pytest.raises(AgentError, match=f"after {MAX_ITERATIONS} iterations"):
        await run_agent(
            system=SYSTEM, user_prompt=USER_PROMPT, llm=llm, books=FakeBookSearch([DUNE])
        )
    assert len(llm.requests) == MAX_ITERATIONS
