"""SQLite storage for the demo: ProfileStore, SearchCache and CoverCache in one file (D-043).

stdlib sqlite3, one connection, one table per port. The port methods are async (D-048)
but call SQLite inline: it is local and fast, so there is no thread hop. Used for local
development and tests; production uses the Postgres adapter. Values are stored as the
models' JSON, so the schema is a few tables.
"""

import sqlite3
import threading
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import TypeAdapter

from book_agent.domain.covers import CachedCover, CoverJob, NewCoverJob
from book_agent.domain.models import CatalogBook
from book_agent.domain.profiles import StoredProfile
from book_agent.migrations import apply_sqlite

_BOOKS = TypeAdapter(list[CatalogBook])


class SqliteStore:
    """ProfileStore, SearchCache and CoverCache over one SQLite database (a file, or
    ":memory:")."""

    def __init__(self, path: Path | str) -> None:
        if isinstance(path, Path):
            path.parent.mkdir(parents=True, exist_ok=True)
        # One connection shared by the event loop's thread and threadpool handlers.
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            # Per connection, off by default: deleting a profile deletes its covers.
            self._db.execute("PRAGMA foreign_keys = ON")
            apply_sqlite(self._db)

    def close(self) -> None:
        self._db.close()

    # --- ProfileStore ---------------------------------------------------------------

    async def save(self, stored: StoredProfile) -> None:
        expires = _utc(stored.expires_at) if stored.expires_at else None
        with self._lock, self._db:  # one transaction: the profile and its covers
            self._db.execute(
                "INSERT INTO profiles (id, body, expires_at) VALUES (?, ?, ?) "
                "ON CONFLICT (id) DO UPDATE SET body = excluded.body, "
                "expires_at = excluded.expires_at",
                (stored.id, stored.model_dump_json(exclude={"covers"}), expires),
            )
            self._db.execute("DELETE FROM profile_covers WHERE profile_id = ?", (stored.id,))
            self._db.executemany(
                "INSERT INTO profile_covers (profile_id, book_index, url) VALUES (?, ?, ?)",
                [(stored.id, index, url) for index, url in stored.covers.items()],
            )

    async def get(self, profile_id: str, now: datetime) -> StoredProfile | None:
        with self._lock:
            row = self._db.execute(
                "SELECT body FROM profiles WHERE id = ?", (profile_id,)
            ).fetchone()
            covers = self._db.execute(
                "SELECT book_index, url FROM profile_covers WHERE profile_id = ?", (profile_id,)
            ).fetchall()
        if row is None:
            return None
        stored = StoredProfile.model_validate_json(row[0]).model_copy(
            update={"covers": dict(covers)}
        )
        if stored.expired(now):
            await self.delete(profile_id)
            return None
        return stored

    async def add_cover(self, profile_id: str, index: int, url: str) -> bool:
        with self._lock, self._db:
            cursor = self._db.execute(
                "INSERT INTO profile_covers (profile_id, book_index, url) "
                "SELECT ?, ?, ? WHERE EXISTS (SELECT 1 FROM profiles WHERE id = ?) "
                "ON CONFLICT (profile_id, book_index) DO UPDATE SET url = excluded.url",
                (profile_id, index, url, profile_id),
            )
        return cursor.rowcount > 0

    async def delete(self, profile_id: str) -> bool:
        with self._lock, self._db:
            cursor = self._db.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
        return cursor.rowcount > 0

    async def purge_expired(self, now: datetime) -> int:
        with self._lock, self._db:
            cursor = self._db.execute(
                "DELETE FROM profiles WHERE expires_at IS NOT NULL AND expires_at <= ?",
                (_utc(now),),
            )
        return cursor.rowcount

    # --- SearchCache ----------------------------------------------------------------

    async def get_search(
        self, query: str, now: datetime, max_age: timedelta
    ) -> list[CatalogBook] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT body, cached_at FROM search_cache WHERE query = ?", (query,)
            ).fetchone()
        if row is None or now - datetime.fromisoformat(row[1]) > max_age:
            return None
        return _BOOKS.validate_json(row[0])

    async def put_search(self, query: str, books: Sequence[CatalogBook], now: datetime) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO search_cache (query, body, cached_at) VALUES (?, ?, ?)",
                (query, _BOOKS.dump_json(list(books)).decode(), _utc(now)),
            )

    # --- CoverCache -----------------------------------------------------------------

    async def get_cover(self, key: str) -> CachedCover | None:
        with self._lock:
            row = self._db.execute(
                "SELECT url, checked_at FROM cover_lookups WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return None
        return CachedCover(url=row[0], checked_at=datetime.fromisoformat(row[1]))

    async def put_cover(self, key: str, cover: CachedCover) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO cover_lookups (key, url, checked_at) VALUES (?, ?, ?)",
                (key, cover.url, _utc(cover.checked_at)),
            )

    # --- CoverJobStore --------------------------------------------------------------

    async def enqueue(self, profile_id: str, jobs: Sequence[NewCoverJob], now: datetime) -> int:
        with self._lock, self._db:
            cursor = self._db.executemany(
                "INSERT INTO cover_jobs (profile_id, book_index, priority, due_at) "
                "SELECT ?, ?, ?, ? WHERE EXISTS (SELECT 1 FROM profiles WHERE id = ?) "
                "ON CONFLICT (profile_id, book_index) DO NOTHING",
                [(profile_id, j.book_index, j.priority, _utc(now), profile_id) for j in jobs],
            )
        return max(cursor.rowcount, 0)

    async def claim(self, now: datetime, lease: timedelta) -> CoverJob | None:
        at = _utc(now)
        with self._lock, self._db:  # one transaction on the only connection: atomic
            row = self._db.execute(
                "SELECT j.id, j.profile_id, j.book_index, j.attempts FROM cover_jobs AS j "
                "JOIN profiles AS p ON p.id = j.profile_id "
                "WHERE j.due_at <= ? AND (j.leased_until IS NULL OR j.leased_until <= ?) "
                "AND (p.expires_at IS NULL OR p.expires_at > ?) "
                "ORDER BY j.priority, j.due_at, j.id LIMIT 1",
                (at, at, at),
            ).fetchone()
            if row is None:
                return None
            self._db.execute(
                "UPDATE cover_jobs SET leased_until = ?, attempts = attempts + 1 WHERE id = ?",
                (_utc(now + lease), row[0]),
            )
        return CoverJob(id=row[0], profile_id=row[1], book_index=row[2], attempts=row[3] + 1)

    async def complete(self, job_id: int) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM cover_jobs WHERE id = ?", (job_id,))

    async def fail(self, job_id: int, retry_at: datetime, max_attempts: int) -> bool:
        with self._lock, self._db:
            given_up = self._db.execute(
                "DELETE FROM cover_jobs WHERE id = ? AND attempts >= ?", (job_id, max_attempts)
            ).rowcount
            if given_up:
                return False
            self._db.execute(
                "UPDATE cover_jobs SET due_at = ?, leased_until = NULL WHERE id = ?",
                (_utc(retry_at), job_id),
            )
        return True

    async def pending(self, profile_id: str, now: datetime) -> int:
        with self._lock:
            row = self._db.execute(
                "SELECT count(*) FROM cover_jobs AS j JOIN profiles AS p ON p.id = j.profile_id "
                "WHERE j.profile_id = ? AND (p.expires_at IS NULL OR p.expires_at > ?)",
                (profile_id, _utc(now)),
            ).fetchone()
        return int(row[0])


def _utc(moment: datetime) -> str:
    """Timestamps are stored as UTC ISO text, so comparing the text compares instants."""
    return moment.astimezone(UTC).isoformat()
