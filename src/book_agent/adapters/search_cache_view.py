"""The SearchCache view of a store whose own get/put names belong to profiles."""

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Protocol

from book_agent.domain.models import CatalogBook


class SearchStore(Protocol):
    async def get_search(
        self, query: str, now: datetime, max_age: timedelta
    ) -> list[CatalogBook] | None: ...

    async def put_search(self, query: str, books: Sequence[CatalogBook], now: datetime) -> None: ...


class SearchCacheView:
    """SearchCache over SqliteStore or PostgresStore."""

    def __init__(self, store: SearchStore) -> None:
        self._store = store

    async def get(self, query: str, now: datetime, max_age: timedelta) -> list[CatalogBook] | None:
        return await self._store.get_search(query, now, max_age)

    async def put(self, query: str, books: Sequence[CatalogBook], now: datetime) -> None:
        await self._store.put_search(query, books, now)
