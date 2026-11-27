"""R-004: a recommendation stream always ends with exactly one terminal event."""

import asyncio
import logging
import signal
from collections.abc import AsyncGenerator, Sequence
from types import FrameType

import pytest

from book_agent import recommendation_stream
from book_agent.agent_run import DEFAULT_AGENT_PROMPT
from book_agent.api.shutdown import drain_on_signals
from book_agent.domain.profiles import StoredProfile
from book_agent.domain.recommendations import Filters
from book_agent.llm.base import LLMClient, LLMResponse, Message, ToolChoice, ToolSpec
from book_agent.recommendation_stream import (
    RESTARTING,
    UNEXPECTED,
    FailedEvent,
    RecommendationEvent,
    recommendation_events,
)
from tests.api_support import NOW, client, services
from tests.fakes import FakeBookSearch, FakeLLM, HangingLLM
from tests.library_data import PROFILE
from tests.test_api_recommendations import PRESET, parse

TERMINAL = {"done", "failed"}


def stored() -> StoredProfile:
    return StoredProfile(id="p", source="goodreads", profile=PROFILE, created_at=NOW)


def run(
    llm: LLMClient, *, deadline_s: float | None = None, stop: asyncio.Event | None = None
) -> AsyncGenerator[RecommendationEvent]:
    return recommendation_events(
        stored(), None, Filters(), llm=llm, books=FakeBookSearch(), works=None,
        prompt=DEFAULT_AGENT_PROMPT, deadline_s=deadline_s, stop=stop,
    )  # fmt: skip


async def collect(llm: LLMClient, *, deadline_s: float | None = None) -> list[RecommendationEvent]:
    return [e async for e in run(llm, deadline_s=deadline_s)]


def only_terminal(events: Sequence[RecommendationEvent]) -> FailedEvent:
    """Exactly one terminal event, and it's the last; returned when it's `failed`."""
    kinds = [e.type for e in events]
    assert sum(k in TERMINAL for k in kinds) == 1 and kinds[-1] in TERMINAL, kinds
    last = events[-1]
    assert isinstance(last, FailedEvent)
    return last


async def test_an_unexpected_exception_in_the_run_ends_with_a_failed_event(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # A bug, not a model or catalog error: the pipeline doesn't catch it.
    failed = only_terminal(await collect(FakeLLM([RuntimeError("bug with Secret Title")])))
    assert (failed.kind, failed.message) == ("agent", UNEXPECTED)
    assert "RuntimeError" in caplog.text  # logged for us, without its message
    assert "Secret Title" not in caplog.text and "Secret Title" not in failed.message


async def test_an_error_while_building_the_result_ends_with_a_failed_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(*_: object) -> RecommendationEvent:
        raise KeyError("missing")

    monkeypatch.setattr(recommendation_stream, "final_event", broken)
    failed = only_terminal(await collect(FakeLLM([RuntimeError("any")])))
    assert failed.kind == "agent"


async def test_a_cancelled_run_ends_with_a_failed_event() -> None:
    class Cancelling(HangingLLM):
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
            raise asyncio.CancelledError

    failed = only_terminal(await collect(Cancelling()))
    assert failed.kind == "unavailable" and "cancelled" in failed.message


async def test_a_server_shutdown_cancels_the_run_and_ends_with_a_failed_event() -> None:
    hanging, stop = HangingLLM(), asyncio.Event()
    events: list[RecommendationEvent] = []
    async for event in run(hanging, stop=stop):
        events.append(event)
        if event.type == "interpreted":
            stop.set()
    failed = only_terminal(events)
    assert (failed.kind, failed.message) == ("unavailable", RESTARTING)
    assert hanging.cancelled == 1


async def test_the_deadline_path_has_exactly_one_terminal_event() -> None:
    only_terminal(await collect(HangingLLM(), deadline_s=0.05))


async def test_a_consumer_that_leaves_cancels_the_run_without_errors() -> None:
    hanging = HangingLLM()
    events = run(hanging)
    assert (await anext(events)).type == "started"
    assert (await anext(events)).type == "interpreted"
    await asyncio.sleep(0)  # let the agent's first request start
    await events.aclose()
    assert hanging.cancelled == 1


async def test_a_draining_server_ends_open_streams_through_the_api() -> None:
    api = services([], presets=PRESET)
    api.llm = HangingLLM()  # mid-run when the server starts draining
    api.draining.set()
    async with client(api) as c:
        response = await c.post("/recommendations", json={"profile_id": "p"})
    events = parse(response.text)
    assert [e["type"] for e in events][-1] == "failed"
    assert sum(e["type"] in TERMINAL for e in events) == 1
    assert events[-1]["message"] == RESTARTING


async def test_sigterm_sets_the_draining_event_and_still_runs_the_servers_handler() -> None:
    calls: list[int] = []

    def server_handler(signum: int, frame: FrameType | None) -> None:
        calls.append(signum)

    original = signal.signal(signal.SIGTERM, server_handler)
    try:
        draining = asyncio.Event()
        restore = drain_on_signals(draining)
        installed = signal.getsignal(signal.SIGTERM)
        assert callable(installed) and installed is not server_handler
        installed(signal.SIGTERM, None)  # what the interpreter does on SIGTERM
        await asyncio.sleep(0)
        assert draining.is_set() and calls == [signal.SIGTERM]
        restore()
        assert signal.getsignal(signal.SIGTERM) is server_handler
    finally:
        signal.signal(signal.SIGTERM, original)


def test_logging_for_unexpected_failures_is_configured() -> None:
    assert recommendation_stream.logger.name == "book_agent.recommendation_stream"
    assert recommendation_stream.logger.getEffectiveLevel() <= logging.ERROR
