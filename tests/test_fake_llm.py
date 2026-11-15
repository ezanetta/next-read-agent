import pytest

from book_agent.llm.base import (
    LLMClient,
    LLMRateLimitError,
    Message,
    StopReason,
    ToolCallPart,
    ToolResultPart,
    ToolSpec,
)
from tests.fakes import FakeLLM, text_response, tool_call_response

SEARCH = ToolSpec(name="search_books", description="Search.", parameters={"type": "object"})
CALL = ToolCallPart(id="call_1", name="search_books", arguments={"query": "dune"})


def test_fake_satisfies_the_protocol() -> None:
    fake = FakeLLM([])
    client: LLMClient = fake  # checked by pyright
    assert client is fake


async def test_plays_back_the_script_in_order() -> None:
    first = tool_call_response(CALL, text="Searching.")
    second = text_response("Here are 3 books.")
    fake = FakeLLM([first, second])

    assert await fake.complete(system="s", messages=[Message.user("hi")], max_tokens=100) == first
    assert await fake.complete(system="s", messages=[Message.user("hi")], max_tokens=100) == second


async def test_records_each_request() -> None:
    fake = FakeLLM([text_response("ok")])
    await fake.complete(
        system="You recommend books.",
        messages=[Message.user("hi")],
        tools=[SEARCH],
        max_tokens=512,
        temperature=0.2,
    )
    request = fake.requests[0]
    assert request.system == "You recommend books."
    assert request.messages == (Message.user("hi"),)
    assert request.tools == (SEARCH,)
    assert request.max_tokens == 512
    assert request.temperature == 0.2


async def test_recorded_messages_are_a_snapshot() -> None:
    fake = FakeLLM([tool_call_response(CALL), text_response("done")])
    history = [Message.user("hi")]

    response = await fake.complete(system="s", messages=history, max_tokens=100)
    history.append(response.message)
    history.append(Message.tool_results(ToolResultPart(tool_call_id="call_1", content="[]")))
    await fake.complete(system="s", messages=history, max_tokens=100)

    assert len(fake.requests[0].messages) == 1  # not mutated by the later appends
    assert len(fake.requests[1].messages) == 3


async def test_raises_scripted_exceptions() -> None:
    fake = FakeLLM([LLMRateLimitError("quota", retry_after=30), text_response("ok")])
    with pytest.raises(LLMRateLimitError) as error:
        await fake.complete(system="s", messages=[Message.user("hi")], max_tokens=100)
    assert error.value.retry_after == 30
    ok = await fake.complete(system="s", messages=[Message.user("hi")], max_tokens=100)
    assert ok.message.text == "ok"
    assert len(fake.requests) == 2


async def test_fails_loudly_when_the_script_runs_out() -> None:
    fake = FakeLLM([text_response("only one")])
    await fake.complete(system="s", messages=[Message.user("hi")], max_tokens=100)
    with pytest.raises(AssertionError, match="script exhausted: call 2 but only 1 scripted"):
        await fake.complete(system="s", messages=[Message.user("hi")], max_tokens=100)


def test_builders_produce_valid_responses() -> None:
    text = text_response("hi")
    assert text.stop_reason is StopReason.END_TURN
    assert text.message.role == "assistant"

    calls = tool_call_response(CALL, ToolCallPart(id="call_2", name="x", arguments={}))
    assert calls.stop_reason is StopReason.TOOL_USE
    assert [c.id for c in calls.message.tool_calls] == ["call_1", "call_2"]
    assert calls.message.text == ""
