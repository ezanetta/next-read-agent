"""R-003: every LLM request has a deadline, and so does every recommendation run."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from time import perf_counter

import pytest
from google import genai
from google.genai import types

from book_agent.agent_run import DEFAULT_AGENT_PROMPT
from book_agent.domain.profiles import StoredProfile
from book_agent.domain.recommendations import Filters
from book_agent.llm.base import LLMClient, LLMNetworkError, Message
from book_agent.llm.gemini import GeminiClient
from book_agent.llm.gemini_embeddings import GeminiEmbedder
from book_agent.llm.retrying import RetryingLLM
from book_agent.llm.timeouts import TimeoutLLM
from book_agent.recommendation_stream import (
    FailedEvent,
    RecommendationEvent,
    recommendation_events,
)
from tests.api_support import NOW
from tests.fakes import FakeBookSearch, FakeLLM, HangingLLM, text_response
from tests.library_data import PROFILE

ASK = [Message.user("hi")]


async def ask(llm: TimeoutLLM) -> None:
    await llm.complete(system="s", messages=ASK, max_tokens=10)


async def test_a_request_that_never_answers_fails_within_the_timeout() -> None:
    hanging = HangingLLM()
    started = perf_counter()
    with pytest.raises(LLMNetworkError, match=r"timed out after 0\.1 s"):
        await ask(TimeoutLLM(hanging, timeout_s=0.1))
    assert perf_counter() - started < 1.0
    assert (hanging.started, hanging.cancelled) == (1, 1)  # the request was cancelled


async def test_a_request_within_the_timeout_is_untouched() -> None:
    reply = text_response("ok")
    llm = TimeoutLLM(FakeLLM([reply]), timeout_s=5)
    assert await llm.complete(system="s", messages=ASK, max_tokens=10) == reply


def test_the_timeout_must_be_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        TimeoutLLM(HangingLLM(), timeout_s=0)


@asynccontextmanager
async def silent_server() -> AsyncGenerator[tuple[str, list[int]]]:
    """A local HTTP server that accepts connections, reads the request and never
    answers: what a stalled Gemini connection looks like to the SDK."""
    connections: list[int] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        connections.append(1)
        try:
            await reader.read(65536)
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}", connections
    finally:
        server.close()


def local_client(base_url: str) -> genai.Client:
    return genai.Client(api_key="test-key", http_options=types.HttpOptions(base_url=base_url))


async def test_the_gemini_adapter_against_a_silent_server_fails_within_the_timeout() -> None:
    async with silent_server() as (base_url, connections):
        llm = TimeoutLLM(GeminiClient(local_client(base_url), model="gemini-test"), timeout_s=0.3)
        started = perf_counter()
        with pytest.raises(LLMNetworkError, match="timed out"):
            await ask(llm)
        assert perf_counter() - started < 3.0
        assert connections  # the request really reached the server


async def test_gemini_embeddings_against_a_silent_server_fail_within_the_timeout() -> None:
    async with silent_server() as (base_url, connections):
        embedder = GeminiEmbedder(local_client(base_url), timeout_s=0.3)
        started = perf_counter()
        with pytest.raises(LLMNetworkError, match="timed out"):
            await embedder.embed(["a book"])
        assert perf_counter() - started < 3.0 and connections


def stored() -> StoredProfile:
    return StoredProfile(id="p", source="goodreads", profile=PROFILE, created_at=NOW)


async def events(
    llm: LLMClient, *, deadline_s: float | None = None
) -> tuple[list[RecommendationEvent], float]:
    started = perf_counter()
    collected = [
        e
        async for e in recommendation_events(
            stored(), None, Filters(), llm=llm, books=FakeBookSearch(), works=None,
            prompt=DEFAULT_AGENT_PROMPT, deadline_s=deadline_s,
        )
    ]  # fmt: skip
    return collected, perf_counter() - started


async def test_a_run_whose_model_never_answers_ends_with_a_failed_event() -> None:
    # Production wiring: retries around a timed-out provider. Both attempts time out.
    llm = RetryingLLM(TimeoutLLM(HangingLLM(), timeout_s=0.1), delay_s=0, jitter=lambda: 0)
    collected, elapsed = await events(llm)
    assert [e.type for e in collected] == ["started", "interpreted", "failed"]
    failed = collected[-1]
    assert isinstance(failed, FailedEvent) and failed.kind == "unavailable"
    assert "timed out" in failed.message and elapsed < 2.0


async def test_a_run_past_its_deadline_is_cancelled_and_ends_with_a_failed_event() -> None:
    hanging = HangingLLM()  # no per-request timeout: only the run's deadline stops it
    collected, elapsed = await events(hanging, deadline_s=0.2)
    assert [e.type for e in collected] == ["started", "interpreted", "failed"]
    failed = collected[-1]
    assert isinstance(failed, FailedEvent) and failed.kind == "unavailable"
    assert "longer than 0.2 s" in failed.message
    assert elapsed < 2.0 and hanging.cancelled == 1  # no quota is spent after the deadline
