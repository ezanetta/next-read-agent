from datetime import date
from pathlib import Path

import pytest
from pydantic import HttpUrl

from book_agent.domain.models import Book, CatalogBook, ReadingStatus
from book_agent.domain.works import WorkInfo
from book_agent.library_enrichment import (
    EnrichmentCache,
    book_key,
    enrich_books,
    load_enrichment,
    save_enrichment,
    to_enrich,
)
from book_agent.ports import BookSearchError
from book_agent.work_keys import CacheEntry, WorkKeyCache
from tests.fakes import FakeBookSearch, FakeWorkDescriptions

TODAY = date(2026, 11, 24)
DUNE = Book(goodreads_id="234225", title="Dune (Dune, #1)", author="Frank Herbert",
            isbn13="9780441013593", my_rating=5.0, status=ReadingStatus.READ)  # fmt: skip
FICCIONES = Book(title="Ficciones", author="Jorge Luis Borges", status=ReadingStatus.READ)
WORK_KEYS = WorkKeyCache(
    entries={"9780441013593": CacheEntry(work="/works/OL893415W", checked=TODAY)}
)


def catalog(title: str, author: str, description: str | None) -> CatalogBook:
    return CatalogBook(source_id=title, title=title, authors=(author,), description=description,
                       categories=("Fiction",), link=HttpUrl("https://example.com"))  # fmt: skip


def test_keys_prefer_the_goodreads_id() -> None:
    assert book_key(DUNE) == "goodreads:234225"
    assert book_key(FICCIONES) == "title:ficciones|jorge luis borges"


async def test_open_library_first_then_google_books() -> None:
    works = FakeWorkDescriptions(
        {"/works/OL893415W": WorkInfo(description="Desert  planet.", subjects=("Arrakis",))}
    )
    search = FakeBookSearch([catalog("Ficciones", "Jorge Luis Borges", "Labyrinths and mirrors.")])
    cache = EnrichmentCache()
    saved: list[int] = []
    summary = await enrich_books(
        [DUNE, FICCIONES], cache, work_keys=WORK_KEYS, works=works, search=search, today=TODAY,
        checkpoint=lambda c: saved.append(len(c.entries)),
    )  # fmt: skip
    dune, ficciones = cache.entries[book_key(DUNE)], cache.entries[book_key(FICCIONES)]
    assert (dune.source, dune.description, dune.subjects) == (
        "open_library",
        "Desert planet.",
        ("Arrakis",),
    )
    assert (ficciones.source, ficciones.subjects) == ("google_books", ("Fiction",))
    assert (summary.open_library, summary.google_books, summary.search_requests) == (1, 1, 1)
    assert search.finds == [("Ficciones", "Jorge Luis Borges")] and saved == [1, 2]


async def test_open_library_subjects_are_kept_when_google_books_has_the_description() -> None:
    works = FakeWorkDescriptions({"/works/OL893415W": WorkInfo(subjects=("Arrakis",))})
    search = FakeBookSearch([catalog("Dune (Dune #1)", "Frank Herbert", "Spice.")])
    cache = EnrichmentCache()
    await enrich_books([DUNE], cache, work_keys=WORK_KEYS, works=works, search=search, today=TODAY)
    entry = cache.entries[book_key(DUNE)]
    assert (entry.source, entry.description, entry.subjects) == (
        "google_books",
        "Spice.",
        ("Arrakis",),
    )


async def test_a_book_nobody_describes_is_cached_as_such() -> None:
    search = FakeBookSearch(
        [catalog("Ficciones", "Jorge Luis Borges", None), catalog("Other", "Borges", "x")]
    )
    cache = EnrichmentCache()
    summary = await enrich_books(
        [FICCIONES], cache, work_keys=WORK_KEYS, works=None, search=search, today=TODAY
    )
    assert cache.entries[book_key(FICCIONES)].source == "none" and summary.undescribed == 1
    assert to_enrich([FICCIONES], cache) == [] and to_enrich([FICCIONES], cache, refresh=True) == [
        FICCIONES
    ]


class _FailingSearch(FakeBookSearch):
    async def find(self, title: str, author: str, *, max_results: int = 10) -> list[CatalogBook]:
        raise BookSearchError("HTTP 429")


async def test_failed_lookups_are_not_cached_so_a_rerun_retries() -> None:
    cache = EnrichmentCache()
    summary = await enrich_books([FICCIONES], cache, work_keys=WORK_KEYS, works=None,
                                 search=_FailingSearch(), today=TODAY)  # fmt: skip
    assert summary.failed == 1 and cache.entries == {}
    works = FakeWorkDescriptions(failing={"/works/OL893415W"})
    summary = await enrich_books([DUNE], cache, work_keys=WORK_KEYS, works=works,
                                 search=FakeBookSearch(), today=TODAY)  # fmt: skip
    assert summary.failed == 1 and cache.entries == {}  # Open Library may have it next time


def test_duplicates_are_enriched_once() -> None:
    assert to_enrich([DUNE, DUNE, FICCIONES], EnrichmentCache()) == [DUNE, FICCIONES]


def test_the_cache_round_trips_and_rejects_garbage(tmp_path: Path) -> None:
    path = tmp_path / "library_books.json"
    assert load_enrichment(path).entries == {}
    cache = EnrichmentCache()
    save_enrichment(cache, path)
    assert load_enrichment(path) == cache
    path.write_text("{not json")
    with pytest.raises(ValueError, match="not a valid enrichment cache"):
        load_enrichment(path)
