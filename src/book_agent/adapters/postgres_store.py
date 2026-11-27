"""Postgres storage for the demo: ProfileStore, the search cache and CoverCache (D-048).

psycopg 3's async API over a small connection pool, so the event loop never blocks on
the database. The schema comes from the numbered migrations (D-049), applied when the
store opens. Same behavior as SqliteStore: tests/contracts/ runs both.
"""

from collections.abc import AsyncGenerator, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any

from psycopg import AsyncConnection
from psycopg.errors import ForeignKeyViolation
from psycopg.rows import TupleRow
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from pydantic import TypeAdapter

from book_agent.domain.covers import CachedCover, CoverJob, NewCoverJob
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

    async def _fetchone(
        self, sql: str, params: Sequence[Any] | Mapping[str, Any]
    ) -> TupleRow | None:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(sql.encode(), params)
            return await cursor.fetchone()

    async def _rowcount(self, sql: str, params: Sequence[Any]) -> int:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(sql.encode(), params)
            return cursor.rowcount

    # --- ProfileStore ---------------------------------------------------------------

    async def save(self, stored: StoredProfile) -> None:
        body = Jsonb(stored.model_dump(mode="json", exclude={"covers"}))
        async with self._pool.connection() as connection, connection.transaction():
            await connection.execute(
                b"""
                INSERT INTO profiles (id, body, expires_at) VALUES (%s, %s, %s)
                ON CONFLICT (id) DO UPDATE
                SET body = EXCLUDED.body, expires_at = EXCLUDED.expires_at
                """,
                (stored.id, body, stored.expires_at),
            )
            await connection.execute(
                b"DELETE FROM profile_covers WHERE profile_id = %s", (stored.id,)
            )
            if stored.covers:
                async with connection.cursor() as cursor:
                    await cursor.executemany(
                        b"INSERT INTO profile_covers (profile_id, book_index, url) "
                        b"VALUES (%s, %s, %s)",
                        [(stored.id, index, url) for index, url in stored.covers.items()],
                    )

    async def get(self, profile_id: str, now: datetime) -> StoredProfile | None:
        row = await self._fetchone(
            """
            SELECT p.body, COALESCE(
                jsonb_object_agg(c.book_index, c.url) FILTER (WHERE c.book_index IS NOT NULL),
                '{}'::jsonb
            )
            FROM profiles AS p LEFT JOIN profile_covers AS c ON c.profile_id = p.id
            WHERE p.id = %s GROUP BY p.id
            """,
            (profile_id,),
        )
        if row is None:
            return None
        stored = StoredProfile.model_validate({**row[0], "covers": row[1]})
        if stored.expired(now):
            await self.delete(profile_id)
            return None
        return stored

    async def add_cover(self, profile_id: str, index: int, url: str) -> bool:
        try:
            return (
                await self._rowcount(
                    """
                    INSERT INTO profile_covers (profile_id, book_index, url)
                    SELECT %s, %s, %s WHERE EXISTS (SELECT 1 FROM profiles WHERE id = %s)
                    ON CONFLICT (profile_id, book_index) DO UPDATE SET url = EXCLUDED.url
                    """,
                    (profile_id, index, url, profile_id),
                )
                > 0
            )
        except ForeignKeyViolation:  # deleted between the check and the insert
            return False

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

    # --- CoverJobStore --------------------------------------------------------------

    async def enqueue(self, profile_id: str, jobs: Sequence[NewCoverJob], now: datetime) -> int:
        if not jobs:
            return 0
        try:
            async with self._pool.connection() as connection, connection.transaction():
                cursor = await connection.execute(
                    b"""
                    INSERT INTO cover_jobs (profile_id, book_index, priority, due_at)
                    SELECT %s, j.book_index, j.priority, %s
                    FROM unnest(%s::int[], %s::int[]) AS j (book_index, priority)
                    WHERE EXISTS (SELECT 1 FROM profiles WHERE id = %s)
                    ON CONFLICT (profile_id, book_index) DO NOTHING
                    """,
                    (
                        profile_id, now, [j.book_index for j in jobs],
                        [j.priority for j in jobs], profile_id,
                    ),
                )  # fmt: skip
                return cursor.rowcount
        except ForeignKeyViolation:  # the profile was deleted meanwhile
            return 0

    async def claim(self, now: datetime, lease: timedelta) -> CoverJob | None:
        # SKIP LOCKED: concurrent claimers never wait on, or get, the same job.
        row = await self._fetchone(
            """
            UPDATE cover_jobs SET leased_until = %(until)s, attempts = attempts + 1
            WHERE id = (
                SELECT j.id FROM cover_jobs AS j JOIN profiles AS p ON p.id = j.profile_id
                WHERE j.due_at <= %(now)s
                  AND (j.leased_until IS NULL OR j.leased_until <= %(now)s)
                  AND (p.expires_at IS NULL OR p.expires_at > %(now)s)
                ORDER BY j.priority, j.due_at, j.id
                LIMIT 1
                FOR UPDATE OF j SKIP LOCKED
            )
            RETURNING id, profile_id, book_index, attempts
            """,
            {"now": now, "until": now + lease},
        )
        if row is None:
            return None
        return CoverJob(id=row[0], profile_id=row[1], book_index=row[2], attempts=row[3])

    async def complete(self, job_id: int) -> None:
        await self._rowcount("DELETE FROM cover_jobs WHERE id = %s", (job_id,))

    async def fail(self, job_id: int, retry_at: datetime, max_attempts: int) -> bool:
        async with self._pool.connection() as connection, connection.transaction():
            given_up = await connection.execute(
                b"DELETE FROM cover_jobs WHERE id = %s AND attempts >= %s", (job_id, max_attempts)
            )
            if given_up.rowcount:
                return False
            await connection.execute(
                b"UPDATE cover_jobs SET due_at = %s, leased_until = NULL WHERE id = %s",
                (retry_at, job_id),
            )
        return True

    async def pending(self, profile_id: str, now: datetime) -> int:
        row = await self._fetchone(
            """
            SELECT count(*) FROM cover_jobs AS j JOIN profiles AS p ON p.id = j.profile_id
            WHERE j.profile_id = %s AND (p.expires_at IS NULL OR p.expires_at > %s)
            """,
            (profile_id, now),
        )
        return int(row[0]) if row is not None else 0
