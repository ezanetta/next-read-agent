"""SearchCache contract: book search results by normalized query, with a maximum age."""

from datetime import UTC, datetime, timedelta

from tests.contracts.conftest import Stores
from tests.library_data import catalog_book

NOW = datetime(2026, 11, 27, 12, tzinfo=UTC)
WEEK = timedelta(days=7)
BOOKS = [
    catalog_book("v1", "Dune", "Frank Herbert", 412),
    catalog_book("v2", "Piranesi", "Susanna Clarke", 272, language="en", isbn13="9781635575637"),
]


async def test_a_miss_is_none(stores: Stores) -> None:
    assert await stores.search.get("dune", NOW, WEEK) is None


async def test_cached_results_come_back_unchanged_while_young_enough(stores: Stores) -> None:
    await stores.search.put("dune", BOOKS, NOW)
    assert await stores.search.get("dune", NOW + WEEK, WEEK) == BOOKS
    assert await stores.search.get("dune", NOW + WEEK + timedelta(seconds=1), WEEK) is None


async def test_an_empty_result_is_cached_too(stores: Stores) -> None:
    await stores.search.put("nothing", [], NOW)
    assert await stores.search.get("nothing", NOW, WEEK) == []


async def test_putting_again_replaces_and_restarts_the_age(stores: Stores) -> None:
    await stores.search.put("dune", BOOKS, NOW)
    later = NOW + timedelta(days=6)
    await stores.search.put("dune", BOOKS[:1], later)
    assert await stores.search.get("dune", later + timedelta(days=6), WEEK) == BOOKS[:1]


async def test_queries_are_kept_apart(stores: Stores) -> None:
    await stores.search.put("dune", BOOKS[:1], NOW)
    await stores.search.put("piranesi", BOOKS[1:], NOW)
    assert await stores.search.get("piranesi", NOW, WEEK) == BOOKS[1:]
