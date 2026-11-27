"""The adapters every port contract runs against (R-009).

Each test gets fresh, empty stores for one adapter, seen only through the ports, so a
contract can't depend on how an adapter is built. SQLite always runs (in memory).
Postgres joins when POSTGRES_TEST_URL is set; otherwise its cases are skipped and say
why.
"""

from collections.abc import AsyncGenerator
from dataclasses import dataclass

import pytest

from book_agent.adapters.postgres_store import PostgresStore
from book_agent.adapters.search_cache_view import SearchCacheView
from book_agent.adapters.sqlite_store import SqliteStore
from book_agent.ports import CoverCache, ProfileStore, SearchCache
from tests.postgres_support import fresh_schema, needs_postgres


@dataclass(frozen=True)
class Stores:
    profiles: ProfileStore
    search: SearchCache
    covers: CoverCache


ADAPTERS = ["sqlite", pytest.param("postgres", marks=needs_postgres)]


@pytest.fixture(params=ADAPTERS)
async def stores(request: pytest.FixtureRequest) -> AsyncGenerator[Stores]:
    if request.param == "sqlite":
        store = SqliteStore(":memory:")
        try:
            yield Stores(profiles=store, search=SearchCacheView(store), covers=store)
        finally:
            store.close()
    else:
        async with fresh_schema() as conninfo, PostgresStore.open(conninfo) as postgres:
            yield Stores(profiles=postgres, search=SearchCacheView(postgres), covers=postgres)
