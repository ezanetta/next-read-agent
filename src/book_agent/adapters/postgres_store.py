"""Postgres storage for the demo: ProfileStore, the search cache and CoverCache (D-048).

psycopg 3's async API over a small connection pool, so the event loop never blocks on
the database. The schema comes from the numbered migrations (D-049), applied when the
store opens. Same behavior as SqliteStore: tests/contracts/ runs both.
"""

from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import TupleRow
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from pydantic import TypeAdapter

from book_agent.domain.covers import CachedCover
from book_agent.domain.models import CatalogBook
from book_agent.domain.profiles import StoredProfile
from book_agent.migrations import apply_postgres

_BOOKS = TypeAdapter(list[CatalogBook])
DEFAULT_POOL_MIN, DEFAULT_POOL_MAX = 1, 4
OPEN_TIMEOUT_S = 10.0

Pool = AsyncConnectionPool[AsyncConnection[TupleRow]]


class PostgresStore:
    """ProfileStore and CoverCache, plus get_search/put_search for SearchCacheView."""

    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    @classmethod
    @asynccontextmanager
    async def open(
        cls,
        conninfo: str,
        *,
        min_size: int = DEFAULT_POOL_MIN,
        max_size: int = DEFAULT_POOL_MAX,
    ) -> AsyncGenerator["PostgresStore"]:
        """Open the pool, apply pending migrations, and close the pool on exit. Raises
        psycopg_pool.PoolTimeout when the database can't be reached in time."""
        pool: Pool = AsyncConnectionPool(
            conninfo,
            min_size=min_size,
            max_size=max_size,
            kwargs={"autocommit": True},  # one statement, one transaction; or explicit ones
            open=False,
        )
        await pool.open(wait=True, timeout=OPEN_TIMEOUT_S)
        try:
            async with pool.connection() as connection:
                await apply_postgres(connection)
            yield cls(pool)
        finally:
            await pool.close()

    async def _fetchone(self, sql: str, params: Sequence[Any]) -> TupleRow | None:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(sql.encode(), params)
            return await cursor.fetchone()

    async def _rowcount(self, sql: str, params: Sequence[Any]) -> int:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(sql.encode(), params)
            return cursor.rowcount

    # --- ProfileStore ---------------------------------------------------------------

    async def save(self, stored: StoredProfile) -> None:
        await self._rowcount(
            """
            INSERT INTO profiles (id, body, expires_at) VALUES (%s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET body = EXCLUDED.body, expires_at = EXCLUDED.expires_at
            """,
            (stored.id, Jsonb(stored.model_dump(mode="json")), stored.expires_at),
        )

    async def get(self, profile_id: str, now: datetime) -> StoredProfile | None:
        row = await self._fetchone("SELECT body FROM profiles WHERE id = %s", (profile_id,))
        if row is None:
            return None
        stored = StoredProfile.model_validate(row[0])
        if stored.expired(now):
            await self.delete(profile_id)
            return None
        return stored

    async def delete(self, profile_id: str) -> bool:
        return await self._rowcount("DELETE FROM profiles WHERE id = %s", (profile_id,)) > 0

    async def purge_expired(self, now: datetime) -> int:
        return await self._rowcount(
            "DELETE FROM profiles WHERE expires_at IS NOT NULL AND expires_at <= %s", (now,)
        )

    # --- Search cache (through SearchCacheView) --------------------------------------

    async def get_search(
        self, query: str, now: datetime, max_age: timedelta
    ) -> list[CatalogBook] | None:
        row = await self._fetchone(
            "SELECT body, cached_at FROM search_cache WHERE query = %s", (query,)
        )
        if row is None or now - row[1] > max_age:
            return None
        return _BOOKS.validate_python(row[0])

    async def put_search(self, query: str, books: Sequence[CatalogBook], now: datetime) -> None:
        await self._rowcount(
            """
            INSERT INTO search_cache (query, body, cached_at) VALUES (%s, %s, %s)
            ON CONFLICT (query) DO UPDATE SET body = EXCLUDED.body, cached_at = EXCLUDED.cached_at
            """,
            (query, Jsonb(_BOOKS.dump_python(list(books), mode="json")), now),
        )

    # --- CoverCache -----------------------------------------------------------------

    async def get_cover(self, key: str) -> CachedCover | None:
        row = await self._fetchone(
            "SELECT url, checked_at FROM cover_lookups WHERE key = %s", (key,)
        )
        return CachedCover(url=row[0], checked_at=row[1]) if row is not None else None

    async def put_cover(self, key: str, cover: CachedCover) -> None:
        await self._rowcount(
            """
            INSERT INTO cover_lookups (key, url, checked_at) VALUES (%s, %s, %s)
            ON CONFLICT (key) DO UPDATE SET url = EXCLUDED.url, checked_at = EXCLUDED.checked_at
            """,
            (key, cover.url, cover.checked_at),
        )
