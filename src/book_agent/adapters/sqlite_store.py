"""SQLite storage for the demo: ProfileStore and SearchCache in one file (D-043).

stdlib sqlite3, one connection, one table per port. Needs a persistent disk and a single
instance; other hosting gets another implementation behind the same ports. Values are
stored as the models' JSON, so the schema is two tables and nothing to migrate.
"""

import sqlite3
import threading
from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import TypeAdapter

from book_agent.domain.models import CatalogBook
from book_agent.domain.profiles import StoredProfile

_BOOKS = TypeAdapter(list[CatalogBook])

_SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
    id TEXT PRIMARY KEY,
    body TEXT NOT NULL,
    expires_at TEXT
);
CREATE TABLE IF NOT EXISTS search_cache (
    query TEXT PRIMARY KEY,
    body TEXT NOT NULL,
    cached_at TEXT NOT NULL
);
"""


class SqliteStore:
    """ProfileStore and SearchCache over one SQLite database (a file, or ":memory:")."""

    def __init__(self, path: Path | str) -> None:
        if isinstance(path, Path):
            path.parent.mkdir(parents=True, exist_ok=True)
        # One connection shared by the event loop's thread and threadpool handlers.
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock, self._db:
            self._db.executescript(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    # --- ProfileStore ---------------------------------------------------------------

    def save(self, stored: StoredProfile) -> None:
        expires = stored.expires_at.isoformat() if stored.expires_at else None
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO profiles (id, body, expires_at) VALUES (?, ?, ?)",
                (stored.id, stored.model_dump_json(), expires),
            )

    def get(self, profile_id: str, now: datetime) -> StoredProfile | None:
        with self._lock:
            row = self._db.execute(
                "SELECT body FROM profiles WHERE id = ?", (profile_id,)
            ).fetchone()
        if row is None:
            return None
        stored = StoredProfile.model_validate_json(row[0])
        if stored.expired(now):
            self.delete(profile_id)
            return None
        return stored

    def delete(self, profile_id: str) -> bool:
        with self._lock, self._db:
            cursor = self._db.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
        return cursor.rowcount > 0

    def purge_expired(self, now: datetime) -> int:
        with self._lock, self._db:
            cursor = self._db.execute(
                "DELETE FROM profiles WHERE expires_at IS NOT NULL AND expires_at <= ?",
                (now.isoformat(),),
            )
        return cursor.rowcount

    # --- SearchCache ----------------------------------------------------------------

    def get_search(self, query: str, now: datetime, max_age: timedelta) -> list[CatalogBook] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT body, cached_at FROM search_cache WHERE query = ?", (query,)
            ).fetchone()
        if row is None or now - datetime.fromisoformat(row[1]) > max_age:
            return None
        return _BOOKS.validate_json(row[0])

    def put_search(self, query: str, books: Sequence[CatalogBook], now: datetime) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO search_cache (query, body, cached_at) VALUES (?, ?, ?)",
                (query, _BOOKS.dump_json(list(books)).decode(), now.isoformat()),
            )


class SqliteSearchCache:
    """The SearchCache view of a SqliteStore (its get/put names belong to profiles)."""

    def __init__(self, store: SqliteStore) -> None:
        self._store = store

    def get(self, query: str, now: datetime, max_age: timedelta) -> list[CatalogBook] | None:
        return self._store.get_search(query, now, max_age)

    def put(self, query: str, books: Sequence[CatalogBook], now: datetime) -> None:
        self._store.put_search(query, books, now)
