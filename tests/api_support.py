"""Building the API with fakes for tests."""

from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from httpx import ASGITransport, AsyncClient

from book_agent.adapters.search_cache_view import SearchCacheView
from book_agent.adapters.sqlite_store import SqliteStore
from book_agent.agent_run import DEFAULT_AGENT_PROMPT
from book_agent.api.app import create_app
from book_agent.api.services import ApiServices
from book_agent.covers import CoverQueue, Pacer
from book_agent.domain.profiles import StoredProfile
from book_agent.llm.base import LLMResponse
from tests.fakes import FakeBookSearch, FakeCoverFinder, FakeLLM, FakeTime
from tests.library_data import catalog

NOW = datetime(2026, 11, 25, 12, tzinfo=UTC)


def services(
    script: Sequence[LLMResponse | Exception] = (),
    *,
    books: FakeBookSearch | None = None,
    presets: dict[str, StoredProfile] | None = None,
    covers: FakeCoverFinder | None = None,
) -> ApiServices:
    """Services with fakes. With `covers`, a real CoverQueue over that finder, paced on
    fake time (so tests don't wait) and sharing the profile store's cover cache."""
    store = SqliteStore(":memory:")
    queue = None
    if covers is not None:
        time = covers.time or FakeTime()
        pacer = Pacer(sleep=time.sleep, monotonic=time.monotonic)
        queue = CoverQueue(covers, store, store, store, pacer=pacer, clock=lambda: NOW)
    return ApiServices(
        profiles=store,
        search_cache=SearchCacheView(store),
        books=books or catalog(),
        llm=FakeLLM(list(script)),
        prompt=DEFAULT_AGENT_PROMPT,
        presets=presets or {},
        covers=queue,
        clock=lambda: NOW,
    )


@asynccontextmanager
async def client(api: ApiServices) -> AsyncGenerator[AsyncClient]:
    transport = ASGITransport(app=create_app(api))
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
