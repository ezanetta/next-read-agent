from datetime import date
from pathlib import Path

import pytest

from book_agent.domain.models import Profile
from book_agent.ports import WorkLookupError
from book_agent.work_keys import (
    CachedWorkResolver,
    CacheEntry,
    Coverage,
    WorkKeyCache,
    coverage,
    format_coverage,
    load_cache,
    resolve_library,
    save_cache,
)
from tests.fakes import FakeWorkResolver
from tests.library_data import book

DAY_ONE, DAY_TWO = date(2026, 11, 15), date(2026, 11, 16)
KAFKA_ES, KAFKA_EN, UNKNOWN_ISBN = "9789871165070", "9780553213690", "9798569694662"
KAFKA_WORK = "/works/OL498556W"


def cached(*entries: tuple[str, str | None]) -> WorkKeyCache:
    return WorkKeyCache(
        entries={isbn: CacheEntry(work=work, checked=DAY_ONE) for isbn, work in entries}
    )


def resolver(
    inner: FakeWorkResolver, cache: WorkKeyCache | None = None, *, refresh: bool = False
) -> CachedWorkResolver:
    return CachedWorkResolver(
        inner, cache or WorkKeyCache(), refresh=refresh, today=lambda: DAY_TWO
    )


# --- File ---------------------------------------------------------------------------


def test_missing_file_is_an_empty_cache(tmp_path: Path) -> None:
    assert load_cache(tmp_path / "work_keys.json") == WorkKeyCache()


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "data" / "work_keys.json"  # the folder is created
    cache = cached((KAFKA_ES, KAFKA_WORK), (UNKNOWN_ISBN, None))
    save_cache(cache, path)
    assert load_cache(path) == cache
    assert [p.name for p in path.parent.iterdir()] == ["work_keys.json"]  # no temp file left


def test_invalid_file_is_an_error_not_a_fresh_start(tmp_path: Path) -> None:
    path = tmp_path / "work_keys.json"
    path.write_text('{"version": 2, "entries": {}}')
    with pytest.raises(ValueError, match="not a valid work key cache"):
        load_cache(path)


# --- CachedWorkResolver ----------------------------------------------------------------


async def test_cached_isbns_are_not_asked_again() -> None:
    inner = FakeWorkResolver()
    works = resolver(inner, cached((KAFKA_ES, KAFKA_WORK), (UNKNOWN_ISBN, None)))
    assert await works.work_for_isbn(KAFKA_ES) == KAFKA_WORK
    assert await works.work_for_isbn(UNKNOWN_ISBN) is None  # a cached "unknown"
    assert inner.lookups == []
    assert not works.changed


async def test_new_isbns_are_asked_and_cached_with_the_date() -> None:
    inner = FakeWorkResolver({KAFKA_EN: KAFKA_WORK})
    works = resolver(inner)
    assert await works.work_for_isbn(KAFKA_EN) == KAFKA_WORK
    assert await works.work_for_isbn(UNKNOWN_ISBN) is None
    assert works.cache.entries == {
        KAFKA_EN: CacheEntry(work=KAFKA_WORK, checked=DAY_TWO),
        UNKNOWN_ISBN: CacheEntry(work=None, checked=DAY_TWO),
    }
    assert works.changed


async def test_failures_are_not_cached() -> None:
    works = resolver(FakeWorkResolver(failing={KAFKA_EN}))
    with pytest.raises(WorkLookupError):
        await works.work_for_isbn(KAFKA_EN)
    assert works.cache.entries == {} and not works.changed


async def test_refresh_asks_each_isbn_once_more() -> None:
    inner = FakeWorkResolver({KAFKA_ES: "/works/OL1W"})
    works = resolver(inner, cached((KAFKA_ES, KAFKA_WORK)), refresh=True)
    assert await works.work_for_isbn(KAFKA_ES) == "/works/OL1W"
    assert await works.work_for_isbn(KAFKA_ES) == "/works/OL1W"
    assert inner.lookups == [KAFKA_ES]
    assert works.cache.entries[KAFKA_ES].checked == DAY_TWO


async def test_failed_refresh_keeps_the_old_entry() -> None:
    works = resolver(
        FakeWorkResolver(failing={KAFKA_ES}), cached((KAFKA_ES, KAFKA_WORK)), refresh=True
    )
    with pytest.raises(WorkLookupError):
        await works.work_for_isbn(KAFKA_ES)
    assert works.cache.entries[KAFKA_ES] == CacheEntry(work=KAFKA_WORK, checked=DAY_ONE)


# --- Library coverage ------------------------------------------------------------------

LIBRARY = Profile(
    name="Reader",
    books=[
        book("La metamorfosis", "Franz Kafka", isbn13=KAFKA_ES),
        book("Metamorphosis", "Franz Kafka", isbn13=UNKNOWN_ISBN),
        book("The Metamorphosis", "Franz Kafka", isbn13=KAFKA_EN),
        book("Ficciones", "Jorge Luis Borges", isbn13=KAFKA_ES),  # a shared ISBN: one lookup
        book("A Kindle edition", "Someone"),  # no ISBN
        book("Bad data", "Someone", isbn13="9789871165071"),  # wrong check digit
    ],
)


def test_coverage_from_the_cache_alone() -> None:
    assert coverage(LIBRARY, cached((KAFKA_ES, KAFKA_WORK), (UNKNOWN_ISBN, None))) == Coverage(
        books=6, with_isbn=4, resolved=2, unknown=1, unchecked=1
    )


async def test_resolve_library_looks_up_each_isbn_once_and_counts_failures() -> None:
    inner = FakeWorkResolver({KAFKA_ES: KAFKA_WORK}, failing={KAFKA_EN})
    works = resolver(inner)

    result, failed = await resolve_library(LIBRARY, works)

    assert sorted(inner.lookups) == sorted([KAFKA_ES, UNKNOWN_ISBN, KAFKA_EN])
    assert result == Coverage(books=6, with_isbn=4, resolved=2, unknown=1, unchecked=1)
    assert failed == 1
    assert not result.complete


def test_format_coverage() -> None:
    result = Coverage(books=226, with_isbn=177, resolved=160, unknown=15, unchecked=2)
    assert format_coverage(result, failed=2) == [
        "Library: 226 books, 177 with an ISBN",
        "  160 resolved to a work, 15 unknown to Open Library, 2 not checked yet",
        "  2 lookups failed: run the works command again to retry",
        "  49 without an ISBN are covered only by the LLM check",
    ]
    assert len(format_coverage(result)) == 3  # no failure line without failures
