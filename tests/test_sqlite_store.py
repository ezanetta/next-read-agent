from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import HttpUrl

from book_agent.adapters.sqlite_store import SqliteSearchCache, SqliteStore
from book_agent.domain.covers import CachedCover
from book_agent.domain.models import CatalogBook
from book_agent.domain.profiles import StoredProfile
from book_agent.ports import CoverCache, ProfileStore, SearchCache
from tests.library_data import PROFILE

NOW = datetime(2026, 11, 25, 12, tzinfo=UTC)


def stored(profile_id: str, expires_at: datetime | None) -> StoredProfile:
    return StoredProfile(id=profile_id, source="manual", profile=PROFILE, covers={0: "c"},
                         created_at=NOW, expires_at=expires_at)  # fmt: skip


async def test_profiles_round_trip_and_expire(tmp_path: Path) -> None:
    store = SqliteStore(tmp_path / "demo" / "demo.sqlite3")
    _: ProfileStore = store  # pyright checks the Protocols here
    __: CoverCache = store
    await store.save(stored("live", NOW + timedelta(days=3)))
    await store.save(stored("old", NOW + timedelta(hours=1)))
    assert await store.get("live", NOW) == stored("live", NOW + timedelta(days=3))
    assert await store.get("old", NOW + timedelta(hours=2)) is None  # expired: gone
    assert await store.get("old", NOW) is None
    assert await store.get("missing", NOW) is None


async def test_purge_and_delete() -> None:
    store = SqliteStore(":memory:")
    await store.save(stored("a", NOW + timedelta(days=1)))
    await store.save(stored("b", NOW - timedelta(seconds=1)))
    await store.save(stored("preset", None))
    assert await store.purge_expired(NOW) == 1
    assert await store.delete("a") and not await store.delete("a")
    assert await store.get("preset", NOW + timedelta(days=999)) is not None


async def test_search_cache_respects_max_age() -> None:
    cache = SqliteSearchCache(SqliteStore(":memory:"))
    _: SearchCache = cache
    book = CatalogBook(source_id="x", title="Dune", authors=("Frank Herbert",),
                       link=HttpUrl("https://example.com"))  # fmt: skip
    await cache.put("dune", [book], NOW)
    assert await cache.get("dune", NOW + timedelta(days=6), timedelta(days=7)) == [book]
    assert await cache.get("dune", NOW + timedelta(days=8), timedelta(days=7)) is None
    assert await cache.get("other", NOW, timedelta(days=7)) is None


async def test_cover_lookups_are_cached_with_their_time_and_survive_reopening(
    tmp_path: Path,
) -> None:
    path = tmp_path / "demo.sqlite3"
    store = SqliteStore(path)
    assert await store.get_cover("dune|frank herbert") is None
    await store.put_cover("dune|frank herbert", CachedCover("https://c/42", NOW))
    await store.put_cover("lost|someone", CachedCover(None, NOW))
    store.close()
    reopened = SqliteStore(path)
    assert await reopened.get_cover("dune|frank herbert") == CachedCover("https://c/42", NOW)
    assert await reopened.get_cover("lost|someone") == CachedCover(None, NOW)
