import asyncio
import json
from collections.abc import AsyncGenerator, Sequence
from typing import Any

from book_agent.api.routes.recommendations import sse
from book_agent.domain.profiles import StoredProfile
from book_agent.llm.base import LLMError, LLMResponse, ToolCallPart
from book_agent.recommendation_stream import RecommendationEvent, StartedEvent
from tests.api_support import NOW, client, services
from tests.fakes import text_response, tool_call_response
from tests.library_data import CLEAN_THREE, PROFILE, rec

PRESET = {"p": StoredProfile(id="p", source="sample", profile=PROFILE, created_at=NOW)}


def interpreted(preference: str) -> LLMResponse:
    reply = {"preference": preference, "language": None, "max_pages": None}
    return text_response(json.dumps(reply))


def submit(books: list[Any], call_id: str = "s-1") -> LLMResponse:
    return tool_call_response(ToolCallPart(
        id=call_id, name="submit_recommendations",
        arguments={"recommendations": [b.model_dump() for b in books]},
    ))  # fmt: skip


def search(query: str) -> LLMResponse:
    return tool_call_response(
        ToolCallPart(id="q-1", name="search_books", arguments={"query": query})
    )


def parse(body: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if not line.startswith(":"))
        data = json.loads(lines["data"])
        assert data["type"] == lines["event"]
        events.append(data)
    return events


async def stream(
    script: Sequence[LLMResponse | Exception], body: dict[str, Any]
) -> tuple[int, str]:
    async with client(services(script, presets=PRESET)) as c:
        response = await c.post("/recommendations", json=body)
        return response.status_code, response.text


async def test_a_run_streams_its_progress_and_ends_with_the_result() -> None:
    dune = rec("Dune", "Frank Herbert", "Like Ficciones.")
    script = [interpreted("space opera"), search("space opera"),
              submit([dune, *CLEAN_THREE[1:]]), submit(CLEAN_THREE, "s-2")]  # fmt: skip
    status, body = await stream(script, {"profile_id": "p", "request": "some space opera"})
    assert status == 200
    events = parse(body)
    assert [e["type"] for e in events] == [
        "started", "interpreted", "searching", "submitted", "checking", "rejected",
        "submitted", "checking", "done",
    ]  # fmt: skip
    assert events[1]["preference"] == "space opera"
    assert events[2]["searches"][0]["query"] == "space opera"
    assert (
        events[5]["problems"][0]["rule"] == "already_read" and events[5]["problems"][0]["book"] == 1
    )
    result = events[-1]["result"]
    assert [b["title"] for b in result["books"]] == [
        "Hyperion",
        "The Left Hand of Darkness",
        "Foundation",
    ]
    first = result["books"][0]
    assert first["link"].startswith("https://books.google.com") and first["reason"]
    assert first["cited"][0] == {
        "title": "Dune (Dune Chronicles, #1)", "author": "Frank Herbert", "rating": 5.0,
        "picked": False, "shelf": "read", "cover_url": None,
    }  # fmt: skip
    trace = result["trace"]
    assert (trace["iterations"], trace["submissions"], len(trace["rejections"])) == (3, 2, 1)
    assert [s["outcome"] for s in trace["steps"]] == ["searched", "rejected", "accepted"]


async def test_a_failed_run_ends_with_a_failed_event() -> None:
    status, body = await stream([LLMError("Gemini API error 503")], {"profile_id": "p"})
    events = parse(body)
    assert status == 200 and [e["type"] for e in events] == ["started", "interpreted", "failed"]
    assert events[-1]["kind"] == "unavailable"


async def test_an_unknown_profile_is_a_404_before_streaming() -> None:
    status, body = await stream([], {"profile_id": "nope"})
    assert status == 404 and json.loads(body)["error"] == "not_found"


async def test_request_text_has_a_size_limit() -> None:
    status, _ = await stream([], {"profile_id": "p", "request": "x" * 501})
    assert status == 422


async def test_sse_sends_keepalives_and_closing_it_cancels_the_events() -> None:
    closed = asyncio.Event()

    async def slow() -> AsyncGenerator[RecommendationEvent]:
        try:
            yield StartedEvent(run_id="r", prompt_id="agent_v3")
            await asyncio.sleep(10)
            yield StartedEvent(run_id="never", prompt_id="agent_v3")
        finally:
            closed.set()

    frames = sse(slow(), keepalive_s=0.01)
    assert (await anext(frames)).startswith("event: started\n")
    assert await anext(frames) == ": keep-alive\n\n"
    await frames.aclose()  # the client went away
    assert closed.is_set()


async def test_the_result_has_no_year_because_the_catalogs_is_the_editions() -> None:
    script = [interpreted("space opera"), submit(CLEAN_THREE)]
    _, body = await stream(script, {"profile_id": "p", "request": "space opera"})
    book = parse(body)[-1]["result"]["books"][0]
    assert "year" not in book and {"pages", "language", "link"} <= set(book)


async def test_searches_report_how_many_books_they_found() -> None:
    script = [interpreted("x"), search("space opera"), submit(CLEAN_THREE)]
    _, body = await stream(script, {"profile_id": "p", "request": "x"})
    searching = next(e for e in parse(body) if e["type"] == "searching")
    assert searching["searches"][0]["found"] == 0  # the fake catalog has no match
    from book_agent.agent import ToolTrace
    from book_agent.recommendation_stream import found

    assert found(ToolTrace(name="search_books", arguments={}, outcome="5 books: A; B")) == 5
    assert found(ToolTrace(name="search_books", arguments={}, outcome="1 book: A")) == 1
    assert found(ToolTrace(name="search_books", arguments={}, outcome="no results")) == 0


async def test_removed_filter_chips_rerun_without_those_filters() -> None:
    reply = {"preference": "x", "language": "en", "max_pages": 600}
    script = [text_response(json.dumps(reply)), submit(CLEAN_THREE)]
    _, body = await stream(
        script, {"profile_id": "p", "request": "x in English", "ignore_filters": ["language"]}
    )
    interpreted_event = parse(body)[1]
    assert interpreted_event["filters"]["language"] is None
    assert interpreted_event["filters"]["max_pages"] == 600
    status, _ = await stream([], {"profile_id": "p", "ignore_filters": ["genre"]})
    assert status == 422
