"""Building the API with fakes for tests."""

from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from httpx import ASGITransport, AsyncClient

from book_agent.adapters.sqlite_store import SqliteSearchCache, SqliteStore
from book_agent.agent_run import DEFAULT_AGENT_PROMPT
from book_agent.api.app import create_app
from book_agent.api.services import ApiServices
from book_agent.domain.profiles import StoredProfile
from book_agent.llm.base import LLMResponse
from tests.fakes import FakeBookSearch, FakeLLM
from tests.library_data import catalog

NOW = datetime(2026, 11, 25, 12, tzinfo=UTC)


def services(
    script: Sequence[LLMResponse | Exception] = (),
    *,
    books: FakeBookSearch | None = None,
    presets: dict[str, StoredProfile] | None = None,
) -> ApiServices:
    store = SqliteStore(":memory:")
    return ApiServices(
        profiles=store,
        search_cache=SqliteSearchCache(store),
        books=books or catalog(),
        llm=FakeLLM(list(script)),
        prompt=DEFAULT_AGENT_PROMPT,
        presets=presets or {},
        clock=lambda: NOW,
    )


@asynccontextmanager
async def client(api: ApiServices) -> AsyncGenerator[AsyncClient]:
    transport = ASGITransport(app=create_app(api))
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
