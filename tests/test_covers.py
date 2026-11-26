import asyncio
from datetime import timedelta

import pytest

from book_agent.adapters.sqlite_store import SqliteStore
from book_agent.covers import MAX_BACKOFF, CoverQueue, Pacer, known_covers, resolve_covers
from book_agent.demo_profiles import store_temporary
from book_agent.domain.covers import (
    NOT_FOUND_MAX_AGE,
    CachedCover,
    cover_id_url,
    cover_key,
)
from book_agent.domain.models import Book, Profile, ReadingStatus
from book_agent.ports import CoverLookupError
from tests.api_support import NOW
from tests.fakes import FakeCoverFinder, FakeTime

READ, TO_READ, READING = ReadingStatus.READ, ReadingStatus.TO_READ, ReadingStatus.CURRENTLY_READING


def library(*books: tuple[str, ReadingStatus]) -> Profile:
    return Profile(
        name="Reader",
        books=[Book(title=t, author=f"Author of {t}", status=s) for t, s in books],
    )


def queue_for(
    finder: FakeCoverFinder, store: SqliteStore, time: FakeTime, interval_s: float = 1.0
) -> CoverQueue:
    pacer = Pacer(interval_s, sleep=time.sleep, monotonic=time.monotonic)
    return CoverQueue(finder, store, store, pacer=pacer, clock=lambda: NOW)


def test_the_cache_key_is_the_normalized_title_and_author() -> None:
    assert cover_key("Guards! Guards! (Discworld #8)", "Terry  Pratchett") == cover_key(
        "guards guards", "terry pratchett"
    )
    assert cover_key("Dune", "Frank Herbert") != cover_key("Dune", "Brian Herbert")


def test_a_missing_cover_is_looked_up_again_after_a_month_and_a_found_one_never() -> None:
    assert CachedCover(None, NOW).fresh(NOW + NOT_FOUND_MAX_AGE - timedelta(seconds=1))
    assert not CachedCover(None, NOW).fresh(NOW + NOT_FOUND_MAX_AGE)
    assert CachedCover(7, NOW).fresh(NOW + timedelta(days=3650))


async def test_the_pacer_spaces_requests_and_backs_off_after_failures() -> None:
    time = FakeTime()
    pacer = Pacer(1.0, sleep=time.sleep, monotonic=time.monotonic)
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


async def test_read_books_are_looked_up_first_then_reading_then_to_read() -> None:
    time = FakeTime()
    finder = FakeCoverFinder({"A": 1, "B": 2, "C": 3, "D": 4}, time)
    store = SqliteStore(":memory:")
    queue = queue_for(finder, store, time)
    profile = library(("A", TO_READ), ("B", READ), ("C", READING), ("D", READ))
    stored = store_temporary(store, "goodreads", profile, NOW)

    assert queue.submit(stored) == 4
    assert queue.pending(stored.id) == 4
    await queue.join()

    assert [title for title, _ in finder.calls] == ["B", "D", "C", "A"]
    saved = store.get(stored.id, NOW)
    assert saved is not None
    assert saved.covers == {i: cover_id_url(i + 1) for i in range(4)}
    assert queue.pending(stored.id) == 0
    await queue.aclose()


async def test_concurrent_uploads_share_one_rate_and_one_cache() -> None:
    time = FakeTime()
    finder = FakeCoverFinder({"Shared": 10, "Mine": 11, "Yours": 12}, time)
    store = SqliteStore(":memory:")
    queue = queue_for(finder, store, time)
    first = store_temporary(store, "goodreads", library(("Shared", READ), ("Mine", READ)), NOW)
    second = store_temporary(store, "goodreads", library(("Shared", READ), ("Yours", READ)), NOW)

    queue.submit(first)
    queue.submit(second)
    await queue.join()

    # "Shared" was looked up once; the second profile got it from the cache.
    assert sorted(t for t, _ in finder.calls) == ["Mine", "Shared", "Yours"]
    gaps = [b - a for a, b in zip(finder.call_times, finder.call_times[1:], strict=False)]
    assert gaps and all(gap >= 1.0 for gap in gaps)
    for stored in (first, second):
        saved = store.get(stored.id, NOW)
        assert saved is not None and saved.covers[0] == cover_id_url(10)
    await queue.aclose()


async def test_cached_books_are_known_at_once_and_never_queued() -> None:
    time = FakeTime()
    finder = FakeCoverFinder({}, time)
    store = SqliteStore(":memory:")
    store.put_cover(cover_key("Known", "Author of Known"), CachedCover(5, NOW))
    store.put_cover(cover_key("Coverless", "Author of Coverless"), CachedCover(None, NOW))
    profile = library(("Known", READ), ("Coverless", READ), ("Picked", READ))
    queue = queue_for(finder, store, time)

    covers = queue.known(profile, {2: "https://search/cover.jpg"})
    assert covers == {0: cover_id_url(5), 2: "https://search/cover.jpg"}
    stored = store_temporary(store, "manual", profile, NOW, covers=covers)
    assert queue.submit(stored) == 0  # "Coverless" was checked recently: no request
    assert queue.pending(stored.id) == 0 and finder.calls == []


async def test_a_failed_lookup_is_not_cached_and_the_worker_keeps_going() -> None:
    time = FakeTime()
    finder = FakeCoverFinder({"Down": CoverLookupError("HTTP 503"), "Up": 3, "None": None}, time)
    store = SqliteStore(":memory:")
    queue = queue_for(finder, store, time)
    stored = store_temporary(
        store, "goodreads", library(("Down", READ), ("Up", READ), ("None", READ)), NOW
    )
    queue.submit(stored)
    await queue.join()

    saved = store.get(stored.id, NOW)
    assert saved is not None and saved.covers == {1: cover_id_url(3)}
    assert store.get_cover(cover_key("Down", "Author of Down")) is None  # retried next time
    assert store.get_cover(cover_key("None", "Author of None")) == CachedCover(None, NOW)
    assert time.sleeps[0] == 2.0  # the failure doubled the next gap
    await queue.aclose()


async def test_a_profile_deleted_while_waiting_costs_no_requests() -> None:
    time = FakeTime()
    finder = FakeCoverFinder({"A": 1}, time)
    store = SqliteStore(":memory:")
    queue = queue_for(finder, store, time)
    stored = store_temporary(store, "goodreads", library(("A", READ)), NOW)
    queue.submit(stored)
    store.delete(stored.id)
    await queue.join()
    assert finder.calls == [] and queue.pending(stored.id) == 0
    await queue.aclose()


async def test_closing_the_queue_stops_the_worker() -> None:
    time = FakeTime()
    store = SqliteStore(":memory:")
    queue = queue_for(FakeCoverFinder({"A": 1}, time), store, time)
    queue.submit(store_temporary(store, "goodreads", library(("A", READ)), NOW))
    await queue.join()
    await queue.aclose()
    await queue.aclose()  # twice is fine
    assert all(t.done() for t in asyncio.all_tasks() if t is not asyncio.current_task())


async def test_resolve_covers_reports_found_missing_and_failed_books() -> None:
    time = FakeTime()
    finder = FakeCoverFinder({"A": 1, "B": None, "C": CoverLookupError("timeout")}, time)
    store = SqliteStore(":memory:")
    store.put_cover(cover_key("D", "Author of D"), CachedCover(4, NOW))
    profile = library(("C", TO_READ), ("A", READ), ("B", READ), ("D", READ))
    progress: list[tuple[int, int]] = []

    report = await resolve_covers(
        profile,
        finder,
        store,
        pacer=Pacer(1.0, sleep=time.sleep, monotonic=time.monotonic),
        clock=lambda: NOW,
        on_progress=lambda done, total: progress.append((done, total)),
    )

    assert report.covers == {1: cover_id_url(1), 3: cover_id_url(4)}
    assert (report.looked_up, report.not_found, report.failed) == (2, 1, 1)
    assert [t for t, _ in finder.calls] == ["A", "B", "C"]  # read first; D was cached
    assert progress == [(1, 3), (2, 3), (3, 3)]
    assert known_covers(profile, {}, store, NOW) == report.covers  # a rerun starts here


@pytest.mark.parametrize("interval", [0.5, 2.0])
async def test_the_queue_keeps_the_configured_interval(interval: float) -> None:
    time = FakeTime()
    finder = FakeCoverFinder({"A": 1, "B": 2, "C": 3}, time)
    store = SqliteStore(":memory:")
    queue = queue_for(finder, store, time, interval_s=interval)
    queue.submit(
        store_temporary(store, "goodreads", library(("A", READ), ("B", READ), ("C", READ)), NOW)
    )
    await queue.join()
    assert time.sleeps == [interval, interval]
    await queue.aclose()
