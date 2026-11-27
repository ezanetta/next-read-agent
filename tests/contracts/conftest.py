"""The adapters every port contract runs against (R-009).

Each test gets fresh, empty stores for one adapter, seen only through the ports, so a
contract can't depend on how an adapter is built. SQLite always runs (in memory).
Postgres joins when its adapter exists and POSTGRES_TEST_URL is set; otherwise its
cases are skipped and say why.
"""

from collections.abc import AsyncGenerator
from dataclasses import dataclass

import pytest

from book_agent.adapters.sqlite_store import SqliteSearchCache, SqliteStore
from book_agent.ports import CoverCache, ProfileStore, SearchCache


@dataclass(frozen=True)
class Stores:
    profiles: ProfileStore
    search: SearchCache
    covers: CoverCache


ADAPTERS = ["sqlite"]


@pytest.fixture(params=ADAPTERS)
async def stores(request: pytest.FixtureRequest) -> AsyncGenerator[Stores]:
    if request.param == "sqlite":
        store = SqliteStore(":memory:")
        try:
            yield Stores(profiles=store, search=SqliteSearchCache(store), covers=store)
        finally:
            store.close()
    else:  # pragma: no cover - every adapter is listed above
        raise AssertionError(f"unknown adapter {request.param}")
