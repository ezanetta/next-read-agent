import asyncio
from datetime import datetime, timedelta

import pytest
from pydantic import HttpUrl

from book_agent.adapters.postgres_store import PostgresStore
from book_agent.adapters.sqlite_store import SqliteStore
from book_agent.covers import (
    LEASE,
    MAX_ATTEMPTS,
    MAX_BACKOFF,
    RETRY_MAX,
    CoverQueue,
    Pacer,
    google_covers,
    known_covers,
    resolve_covers,
    retry_delay,
)
from book_agent.demo_profiles import store_temporary
from book_agent.domain.covers import (
    NOT_FOUND_MAX_AGE,
    CachedCover,
    cover_id_url,
    cover_key,
    google_cover_key,
)
from book_agent.domain.models import Book, Profile, ReadingStatus
from book_agent.ports import BookSearchError, CoverLookupError
from tests.api_support import NOW
from tests.fakes import FakeBookSearch, FakeCoverFinder, FakeTime
from tests.library_data import catalog_book
from tests.postgres_support import fresh_schema, needs_postgres

READ, TO_READ, READING = ReadingStatus.READ, ReadingStatus.TO_READ, ReadingStatus.CURRENTLY_READING
ISBN = "9780000000002"


def book(title: str, status: ReadingStatus = READ, isbn13: str | None = None) -> Book:
    return Book(title=title, author=f"Author of {title}", status=status, isbn13=isbn13)


def library(*books: Book | tuple[str, ReadingStatus]) -> Profile:
    return Profile(
        name="Reader", books=[b if isinstance(b, Book) else book(b[0], b[1]) for b in books]
    )


def fake_pacer(time: FakeTime, interval_s: float = 1.0) -> Pacer:
    return Pacer(interval_s, sleep=time.sleep, monotonic=time.monotonic)


def queue_for(
    finder: FakeCoverFinder, store: SqliteStore, time: FakeTime, interval_s: float = 1.0
) -> CoverQueue:
    return CoverQueue(
        finder, store, store, store, pacer=fake_pacer(time, interval_s), clock=lambda: NOW
    )


def test_the_cache_key_is_the_isbn_or_the_normalized_title_and_author() -> None:
    assert cover_key(book("Dune", isbn13=ISBN)) == f"isbn:{ISBN}"
    assert (
        cover_key(Book(title="Dune", author="X", isbn="0000000000", status=READ)) == f"isbn:{ISBN}"
    )
    no_isbn = Book(title="Guards! Guards! (Discworld #8)", author="Terry  Pratchett", status=READ)
    assert cover_key(no_isbn) == cover_key(
        Book(title="guards guards", author="terry pratchett", status=READ)
    )
    assert google_cover_key(no_isbn) == f"google:{cover_key(no_isbn)}"


def test_a_missing_cover_is_looked_up_again_after_a_month_and_a_found_one_never() -> None:
    assert CachedCover(None, NOW).fresh(NOW + NOT_FOUND_MAX_AGE - timedelta(seconds=1))
    assert not CachedCover(None, NOW).fresh(NOW + NOT_FOUND_MAX_AGE)
    assert CachedCover("https://c/1", NOW).fresh(NOW + timedelta(days=3650))


async def test_the_pacer_spaces_requests_and_backs_off_after_failures() -> None:
    time = FakeTime()
    pacer = fake_pacer(time)
    await pacer.wait()  # the first request doesn't wait
    await pacer.wait()
    assert time.sleeps == [1.0]
    pacer.failed()
    pacer.failed()
    await pacer.wait()
    assert time.sleeps[-1] == 4.0
    for _ in range(10):
        pacer.failed()
    await pacer.wait()
    assert time.sleeps[-1] == MAX_BACKOFF
    pacer.succeeded()
    await pacer.wait()
    assert time.sleeps[-1] == 1.0


async def test_a_book_with_an_isbn_gets_its_own_editions_cover_without_a_search() -> None:
    time = FakeTime()
    # The search would return the work's cover, a translation: it must not be asked.
    finder = FakeCoverFinder({"King Sorrow": 666}, time, editions={ISBN: 7})
    store = SqliteStore(":memory:")
    report = await resolve_covers(
        library(book("King Sorrow", isbn13=ISBN)), finder, store,
        pacer=fake_pacer(time), clock=lambda: NOW,
    )  # fmt: skip
    assert report.covers == {0: cover_id_url(7)}
    assert finder.calls == [("edition", ISBN)]
    assert await store.get_cover(f"isbn:{ISBN}") == CachedCover(cover_id_url(7), NOW)


async def test_an_edition_without_a_cover_falls_back_to_search_paced_as_two_requests() -> None:
    time = FakeTime()
    finder = FakeCoverFinder({"Coverless edition": 8}, time, editions={ISBN: None})
    store = SqliteStore(":memory:")
    report = await resolve_covers(
        library(book("Coverless edition", isbn13=ISBN)), finder, store,
        pacer=fake_pacer(time), clock=lambda: NOW,
    )  # fmt: skip
    assert report.covers == {0: cover_id_url(8)}
    assert finder.calls == [("edition", ISBN), ("search", "Coverless edition")]
    assert finder.call_times[1] - finder.call_times[0] >= 1.0


async def test_read_books_are_looked_up_first_then_reading_then_to_read() -> None:
    time = FakeTime()
    finder = FakeCoverFinder({"A": 1, "B": 2, "C": 3, "D": 4}, time)
    store = SqliteStore(":memory:")
    queue = queue_for(finder, store, time)
    profile = library(("A", TO_READ), ("B", READ), ("C", READING), ("D", READ))
    stored = await store_temporary(store, "goodreads", profile, NOW)

    assert await queue.submit(stored) == 4
    assert await queue.pending(stored.id) == 4
    await queue.run_until_idle()

    assert finder.searched == ["B", "D", "C", "A"]
    saved = await store.get(stored.id, NOW)
    assert saved is not None
    assert saved.covers == {i: cover_id_url(i + 1) for i in range(4)}
    assert await queue.pending(stored.id) == 0
    await queue.aclose()


async def test_concurrent_uploads_share_one_rate_and_one_cache() -> None:
    time = FakeTime()
    finder = FakeCoverFinder({"Shared": 10, "Mine": 11, "Yours": 12}, time, editions={ISBN: None})
    store = SqliteStore(":memory:")
    queue = queue_for(finder, store, time)
    first = await store_temporary(
        store, "goodreads", library(("Shared", READ), book("Mine", isbn13=ISBN)), NOW
    )
    second = await store_temporary(
        store, "goodreads", library(("Shared", READ), ("Yours", READ)), NOW
    )

    await queue.submit(first)
    await queue.submit(second)
    await queue.run_until_idle()

    # "Shared" was looked up once; the second profile got it from the cache.
    assert sorted(finder.searched) == ["Mine", "Shared", "Yours"]
    assert len(finder.calls) == 4  # plus Mine's edition lookup
    gaps = [b - a for a, b in zip(finder.call_times, finder.call_times[1:], strict=False)]
    assert gaps and all(gap >= 1.0 for gap in gaps)
    for stored in (first, second):
        saved = await store.get(stored.id, NOW)
        assert saved is not None and saved.covers[0] == cover_id_url(10)
    await queue.aclose()


async def test_cached_books_are_known_at_once_and_never_queued() -> None:
    time = FakeTime()
    finder = FakeCoverFinder({}, time)
    store = SqliteStore(":memory:")
    known, coverless = book("Known", isbn13=ISBN), book("Coverless")
    await store.put_cover(cover_key(known), CachedCover(cover_id_url(5), NOW))
    await store.put_cover(cover_key(coverless), CachedCover(None, NOW))
    profile = library(known, coverless, ("Picked", READ))
    queue = queue_for(finder, store, time)

    covers = await queue.known(profile, {2: "https://search/cover.jpg"})
    assert covers == {0: cover_id_url(5), 2: "https://search/cover.jpg"}
    stored = await store_temporary(store, "manual", profile, NOW, covers=covers)
    assert await queue.submit(stored) == 0  # "Coverless" was checked recently: no request
    assert await queue.pending(stored.id) == 0 and finder.calls == []


async def test_a_failed_lookup_is_not_cached_and_the_worker_keeps_going() -> None:
    time = FakeTime()
    finder = FakeCoverFinder(
        {"Up": 3, "None": None}, time, editions={ISBN: CoverLookupError("HTTP 503")}
    )
    store = SqliteStore(":memory:")
    queue = queue_for(finder, store, time)
    down = book("Down", isbn13=ISBN)
    stored = await store_temporary(
        store, "goodreads", library(down, ("Up", READ), ("None", READ)), NOW
    )
    await queue.submit(stored)
    await queue.run_until_idle()

    saved = await store.get(stored.id, NOW)
    assert saved is not None and saved.covers == {1: cover_id_url(3)}
    assert await store.get_cover(cover_key(down)) is None  # retried next time, no search either
    assert ("search", "Down") not in finder.calls
    assert await store.get_cover(cover_key(book("None"))) == CachedCover(None, NOW)
    assert time.sleeps[0] == 2.0  # the failure doubled the next gap
    await queue.aclose()


async def test_a_profile_deleted_while_waiting_costs_no_requests() -> None:
    time = FakeTime()
    finder = FakeCoverFinder({"A": 1}, time)
    store = SqliteStore(":memory:")
    queue = queue_for(finder, store, time)
    stored = await store_temporary(store, "goodreads", library(("A", READ)), NOW)
    await queue.submit(stored)
    await store.delete(stored.id)
    await queue.run_until_idle()
    assert finder.calls == [] and await queue.pending(stored.id) == 0
    await queue.aclose()


async def test_the_background_worker_picks_up_new_jobs_and_stops_cleanly() -> None:
    time = FakeTime()
    store = SqliteStore(":memory:")
    queue = queue_for(FakeCoverFinder({"A": 1}, time), store, time)
    queue.start()
    stored = await store_temporary(store, "goodreads", library(("A", READ)), NOW)
    await queue.submit(stored)  # wakes the worker: no polling interval to wait for
    for _ in range(100):
        if await queue.pending(stored.id) == 0:
            break
        await asyncio.sleep(0.01)
    saved = await store.get(stored.id, NOW)
    assert saved is not None and saved.covers == {0: cover_id_url(1)}
    await queue.aclose()
    await queue.aclose()  # twice is fine
    assert all(t.done() for t in asyncio.all_tasks() if t is not asyncio.current_task())


class Clock:
    """A clock the test moves by hand, for retry times and leases."""

    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


def queue_on(finder: FakeCoverFinder, store: SqliteStore, clock: Clock) -> CoverQueue:
    time = finder.time or FakeTime()
    return CoverQueue(finder, store, store, store, pacer=fake_pacer(time), clock=clock)


async def test_a_failed_lookup_is_retried_with_backoff_then_succeeds() -> None:
    finder = FakeCoverFinder({"A": CoverLookupError("HTTP 503")}, FakeTime())
    store, clock = SqliteStore(":memory:"), Clock()
    queue = queue_on(finder, store, clock)
    stored = await store_temporary(store, "goodreads", library(("A", READ)), NOW)
    await queue.submit(stored)

    assert await queue.run_until_idle() == 1  # failed; due again in 30 s
    assert await queue.pending(stored.id) == 1
    clock.now = NOW + retry_delay(1) - timedelta(seconds=1)
    assert await queue.run_until_idle() == 0  # not due yet
    finder.searches["A"] = 9  # Open Library is back
    clock.now = NOW + retry_delay(1)
    assert await queue.run_until_idle() == 1
    saved = await store.get(stored.id, NOW)
    assert saved is not None and saved.covers == {0: cover_id_url(9)}
    assert await queue.pending(stored.id) == 0
    assert retry_delay(2) == 2 * retry_delay(1) and retry_delay(50) == RETRY_MAX


async def test_a_lookup_that_keeps_failing_is_given_up_after_max_attempts() -> None:
    finder = FakeCoverFinder({"A": CoverLookupError("HTTP 503")}, FakeTime())
    store, clock = SqliteStore(":memory:"), Clock()
    queue = queue_on(finder, store, clock)
    stored = await store_temporary(store, "goodreads", library(("A", READ)), NOW)
    await queue.submit(stored)
    for _ in range(MAX_ATTEMPTS):
        assert await queue.run_until_idle() == 1
        clock.now += RETRY_MAX
    assert await queue.pending(stored.id) == 0  # given up: the book keeps its placeholder
    assert await queue.run_until_idle() == 0
    assert len(finder.calls) == MAX_ATTEMPTS
    saved = await store.get(stored.id, clock.now)
    assert saved is not None and saved.covers == {}


async def test_a_restarted_server_resumes_the_pending_jobs() -> None:
    store, clock = SqliteStore(":memory:"), Clock()
    before = queue_on(FakeCoverFinder({}, FakeTime()), store, clock)
    stored = await store_temporary(store, "goodreads", library(("A", READ), ("B", TO_READ)), NOW)
    assert await before.submit(stored) == 2
    await before.aclose()  # the app scaled to zero before any lookup

    finder = FakeCoverFinder({"A": 1, "B": 2}, FakeTime())
    after = queue_on(finder, store, clock)  # a new process: nothing in memory
    assert await after.pending(stored.id) == 2  # counted from the store
    assert await after.run_until_idle() == 2
    saved = await store.get(stored.id, NOW)
    assert saved is not None and saved.covers == {0: cover_id_url(1), 1: cover_id_url(2)}


async def test_a_job_claimed_by_a_dead_process_is_done_after_its_lease() -> None:
    store, clock = SqliteStore(":memory:"), Clock()
    stored = await store_temporary(store, "goodreads", library(("A", READ)), NOW)
    queue = queue_on(FakeCoverFinder({"A": 1}, FakeTime()), store, clock)
    await queue.submit(stored)
    assert await store.claim(NOW, LEASE) is not None  # claimed, then the process died

    assert await queue.run_until_idle() == 0  # still leased
    clock.now = NOW + LEASE
    assert await queue.run_until_idle() == 1
    saved = await store.get(stored.id, clock.now)
    assert saved is not None and saved.covers == {0: cover_id_url(1)}


async def test_resolve_covers_reports_found_missing_and_failed_books() -> None:
    time = FakeTime()
    finder = FakeCoverFinder({"A": 1, "B": None, "C": CoverLookupError("timeout")}, time)
    store = SqliteStore(":memory:")
    await store.put_cover(cover_key(book("D")), CachedCover(cover_id_url(4), NOW))
    profile = library(("C", TO_READ), ("A", READ), ("B", READ), ("D", READ))
    progress: list[tuple[int, int]] = []

    report = await resolve_covers(
        profile, finder, store, pacer=fake_pacer(time), clock=lambda: NOW,
        on_progress=lambda done, total: progress.append((done, total)),
    )  # fmt: skip

    assert report.covers == {1: cover_id_url(1), 3: cover_id_url(4)}
    assert (report.looked_up, report.not_found, report.failed) == (2, 1, 1)
    assert finder.searched == ["A", "B", "C"]  # read first; D was cached
    assert progress == [(1, 3), (2, 3), (3, 3)]
    assert await known_covers(profile, {}, store, NOW) == report.covers  # a rerun starts here


@pytest.mark.parametrize("interval", [0.5, 2.0])
async def test_the_queue_keeps_the_configured_interval(interval: float) -> None:
    time = FakeTime()
    finder = FakeCoverFinder({"A": 1, "B": 2, "C": 3}, time)
    store = SqliteStore(":memory:")
    queue = queue_for(finder, store, time, interval_s=interval)
    await queue.submit(
        await store_temporary(
            store, "goodreads", library(("A", READ), ("B", READ), ("C", READ)), NOW
        )
    )
    await queue.run_until_idle()
    assert time.sleeps == [interval, interval]
    await queue.aclose()


# --- The owner export's Google Books fallback -----------------------------------------


def google_book(source_id: str, title: str, author: str, isbn13: str | None = None):
    found = catalog_book(source_id, title, author, 300, isbn13=isbn13)
    return found.model_copy(update={"cover_url": HttpUrl(f"https://books.google/{source_id}.jpg")})


async def test_google_covers_fill_only_the_missing_books_by_isbn_or_title_and_are_cached() -> None:
    time = FakeTime()
    store = SqliteStore(":memory:")
    google = FakeBookSearch(
        [
            google_book("ed", "Some Edition", "Author of Some Edition", isbn13=ISBN),
            google_book("tr", "El rey dolor", "Author of King Sorrow"),  # a translation
            google_book("en", "King Sorrow", "Author of King Sorrow"),
        ]
    )
    profile = library(book("Has cover"), book("By ISBN", isbn13=ISBN), book("King Sorrow"),
                      book("Nowhere"))  # fmt: skip

    report = await google_covers(
        profile, {0: "https://ol/0"}, google, store, pacer=fake_pacer(time, 0.7),
        clock=lambda: NOW,
    )  # fmt: skip

    assert report.covers == {
        0: "https://ol/0",
        1: "https://books.google/ed.jpg",
        2: "https://books.google/en.jpg",  # the matching title, not the translation
    }
    assert (report.looked_up, report.not_found, report.failed) == (3, 1, 0)
    assert google.searches[0][0] == f"isbn:{ISBN}"
    assert time.sleeps == pytest.approx([0.7, 0.7])
    calls = len(google.searches) + len(google.finds)
    again = await google_covers(profile, {0: "https://ol/0"}, google, store, clock=lambda: NOW)
    assert again.covers == report.covers
    assert len(google.searches) + len(google.finds) == calls  # all from the cache


async def test_google_cover_failures_are_counted_and_not_cached() -> None:
    class Down(FakeBookSearch):
        async def find(self, title: str, author: str, *, max_results: int = 10):
            raise BookSearchError("quota")

    store = SqliteStore(":memory:")
    profile = library(book("Lost"))
    report = await google_covers(
        profile, {}, Down(), store, pacer=fake_pacer(FakeTime()), clock=lambda: NOW
    )
    assert (report.failed, report.covers) == (1, {})
    assert await store.get_cover(google_cover_key(profile.books[0])) is None


@needs_postgres
async def test_on_postgres_a_restarted_worker_resumes_and_saves_covers() -> None:
    async with fresh_schema() as conninfo:
        async with PostgresStore.open(conninfo) as store:
            stored = await store_temporary(store, "goodreads", library(("A", READ)), NOW)
            first = CoverQueue(FakeCoverFinder({}), store, store, store, clock=lambda: NOW)
            assert await first.submit(stored) == 1
        # The app stopped (scale to zero) and starts again with a new pool.
        async with PostgresStore.open(conninfo) as store:
            time = FakeTime()
            finder = FakeCoverFinder({"A": 5}, time)
            again = CoverQueue(
                finder, store, store, store, pacer=fake_pacer(time), clock=lambda: NOW
            )
            assert await again.pending(stored.id) == 1
            assert await again.run_until_idle() == 1
            saved = await store.get(stored.id, NOW)
    assert saved is not None and saved.covers == {0: cover_id_url(5)}
