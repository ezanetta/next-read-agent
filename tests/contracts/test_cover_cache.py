"""CoverCache contract: cover lookups by key, found or not, with when they were made."""

from datetime import UTC, datetime, timedelta

from book_agent.domain.covers import CachedCover
from tests.contracts.conftest import Stores

NOW = datetime(2026, 11, 27, 12, tzinfo=UTC)


async def test_an_unknown_key_is_none(stores: Stores) -> None:
    assert await stores.covers.get_cover("isbn:9780000000002") is None


async def test_found_and_not_found_lookups_come_back_unchanged(stores: Stores) -> None:
    found = CachedCover("https://covers.openlibrary.org/b/id/7-M.jpg", NOW)
    missing = CachedCover(None, NOW - timedelta(days=2))
    await stores.covers.put_cover("isbn:9780000000002", found)
    await stores.covers.put_cover("title:dune|frank herbert", missing)
    assert await stores.covers.get_cover("isbn:9780000000002") == found
    assert await stores.covers.get_cover("title:dune|frank herbert") == missing


async def test_putting_again_replaces_the_lookup(stores: Stores) -> None:
    await stores.covers.put_cover("k", CachedCover(None, NOW))
    later = CachedCover("https://c/1", NOW + timedelta(days=31))
    await stores.covers.put_cover("k", later)
    assert await stores.covers.get_cover("k") == later
