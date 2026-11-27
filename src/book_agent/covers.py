"""Resolving covers to Open Library cover ids, politely (D-046).

- `CoverQueue`: one per server. Uploaded profiles are saved first, then their books
  without a cover become persisted jobs (R-001); a single worker looks them up one
  request per second, read books first, and saves each cover as it's found. Concurrent
  uploads share the queue, so they never raise the request rate, and a restart only
  pauses the jobs.
- `resolve_covers`: the same lookups for a whole profile in one go, for the owner export,
  and `google_covers`: the export's fallback for books Open Library has no cover for.

A lookup is the edition record by ISBN, then (no ISBN, or no cover there) a search by
title and author: up to two requests, each paced. Both check the shared cache first, by
ISBN or by normalized title and author, so a book any profile already resolved costs no
request.
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from book_agent.domain.covers import (
    SHELF_PRIORITY,
    CachedCover,
    CoverJob,
    NewCoverJob,
    book_isbn13,
    cover_id_url,
    cover_key,
    google_cover_key,
)
from book_agent.domain.matching import same_work, titles_match
from book_agent.domain.models import Book, CatalogBook, Profile
from book_agent.domain.profiles import StoredProfile
from book_agent.log_privacy import frames, ref
from book_agent.ports import (
    BookSearch,
    BookSearchError,
    CoverCache,
    CoverFinder,
    CoverJobStore,
    CoverLookupError,
    ProfileStore,
)

logger = logging.getLogger(__name__)

REQUEST_INTERVAL_S = 1.0
GOOGLE_BOOKS_INTERVAL_S = 0.7  # under Google Books' 100 requests a minute
GOOGLE_BOOKS_RESULTS = 5
MAX_BACKOFF = 32  # after repeated failures, at most one request every 32 s

Sleep = Callable[[float], Awaitable[None]]
Clock = Callable[[], datetime]


class Pacer:
    """At most one request per interval. Each failure doubles the interval (up to
    MAX_BACKOFF times), and a success restores it, so an outage isn't hammered."""

    def __init__(
        self,
        interval_s: float = REQUEST_INTERVAL_S,
        *,
        sleep: Sleep = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._interval = interval_s
        self._sleep = sleep
        self._monotonic = monotonic
        self._factor = 1
        self._next = 0.0

    async def wait(self) -> None:
        delay = self._next - self._monotonic()
        if delay > 0:
            await self._sleep(delay)
        self._next = self._monotonic() + self._interval * self._factor

    def succeeded(self) -> None:
        self._factor = 1
        self._next = min(self._next, self._monotonic() + self._interval)

    def failed(self) -> None:
        self._factor = min(self._factor * 2, MAX_BACKOFF)
        self._next = self._monotonic() + self._interval * self._factor


async def cached_url(cache: CoverCache, book: Book, now: datetime) -> str | None:
    """The cover URL when the cache already answers for this book, else None."""
    cached = await cache.get_cover(cover_key(book))
    return cached.url if cached is not None and cached.fresh(now) else None


async def _needs_lookup(cache: CoverCache, book: Book, now: datetime) -> bool:
    cached = await cache.get_cover(cover_key(book))
    return cached is None or not cached.fresh(now)


async def _paced[T](pacer: Pacer, request: Callable[[], Awaitable[T]]) -> T:
    """One request at the pacer's rate; a CoverLookupError slows the next one down."""
    await pacer.wait()
    try:
        result = await request()
    except CoverLookupError:
        pacer.failed()
        raise
    pacer.succeeded()
    return result


async def _look_up(
    finder: CoverFinder, cache: CoverCache, pacer: Pacer, book: Book, now: Clock
) -> str | None:
    """The book's cover URL: its edition's cover by ISBN, else search by title and author.
    Each request is paced; the answer is cached. Raises CoverLookupError (not cached)."""
    cover_id: int | None = None
    isbn = book_isbn13(book)
    if isbn:
        cover_id = await _paced(pacer, lambda: finder.edition_cover_id(isbn))
    if cover_id is None:
        cover_id = await _paced(pacer, lambda: finder.search_cover_id(book.title, book.author))
    url = cover_id_url(cover_id) if cover_id is not None else None
    await cache.put_cover(cover_key(book), CachedCover(url, now()))
    return url


def in_priority_order(profile: Profile) -> list[int]:
    """Book indexes: read first, then currently reading, then to-read; library order within."""
    books = profile.books
    return sorted(range(len(books)), key=lambda i: SHELF_PRIORITY[books[i].status])


async def known_covers(
    profile: Profile, covers: dict[int, str], cache: CoverCache, now: datetime
) -> dict[int, str]:
    """`covers` plus every cover the cache already has. Makes no requests."""
    found = dict(covers)
    for index, book in enumerate(profile.books):
        if index not in found and (url := await cached_url(cache, book, now)):
            found[index] = url
    return found


LEASE = timedelta(minutes=2)  # covers the pacer's longest wait plus two timed-out requests
MAX_ATTEMPTS = 5
RETRY_BASE = timedelta(seconds=30)
RETRY_MAX = timedelta(hours=1)
IDLE_POLL_S = 30.0  # with nothing due, look again this often (retries come due later)


def retry_delay(attempts: int) -> timedelta:
    """30 s after the first failure, doubling each time, at most an hour."""
    doublings = min(max(attempts - 1, 0), 16)  # past RETRY_MAX long before; no overflow
    return min(RETRY_BASE * 2**doublings, RETRY_MAX)


class CoverQueue:
    """The server's cover worker, over persisted jobs (R-001, D-047).

    Uploads enqueue jobs and return; the worker claims due jobs one at a time, looks each
    book up at the pacer's rate (kept in memory: one replica), saves the cover, and
    completes the job. A failed lookup is retried with backoff, then given up. Jobs live
    in the store, so a restart or scale to zero only pauses them: `start` resumes them, and
    a job a dead process had claimed comes back when its lease expires. Pending counts
    come from the store, never from memory.
    """

    def __init__(
        self,
        finder: CoverFinder,
        cache: CoverCache,
        store: ProfileStore,
        jobs: CoverJobStore,
        *,
        pacer: Pacer | None = None,
        clock: Clock,
        idle_poll_s: float = IDLE_POLL_S,
    ) -> None:
        self._finder = finder
        self._cache = cache
        self._store = store
        self._jobs = jobs
        self._pacer = pacer or Pacer()
        self._clock = clock
        self._idle_poll_s = idle_poll_s
        self._wake = asyncio.Event()
        self._worker: asyncio.Task[None] | None = None

    async def known(self, profile: Profile, covers: dict[int, str]) -> dict[int, str]:
        return await known_covers(profile, covers, self._cache, self._clock())

    async def submit(self, stored: StoredProfile) -> int:
        """Queue the books with no cover that the cache can't answer; returns how many.
        Never waits for a lookup."""
        now = self._clock()
        new: list[NewCoverJob] = []
        for index in in_priority_order(stored.profile):
            book = stored.profile.books[index]
            if index in stored.covers or not await _needs_lookup(self._cache, book, now):
                continue
            new.append(NewCoverJob(book_index=index, priority=SHELF_PRIORITY[book.status]))
        added = await self._jobs.enqueue(stored.id, new, now) if new else 0
        if added:
            self._wake.set()
        return added

    async def pending(self, profile_id: str) -> int:
        """Lookups left for this profile, from the store; 0 when its covers are done."""
        return await self._jobs.pending(profile_id, self._clock())

    def start(self) -> None:
        """Run the worker in the background: it resumes jobs left from before a restart."""
        if self._worker is None or self._worker.done():
            self._worker = asyncio.get_running_loop().create_task(self._run())

    async def aclose(self) -> None:
        """Stop the worker. A job it was working on keeps its lease and is claimed again
        when the lease expires."""
        if self._worker is not None:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
            self._worker = None

    async def run_until_idle(self) -> int:
        """Work through every job due now (tests, and the worker's inner loop). Returns how
        many jobs were handled."""
        handled = 0
        while await self.work_once():
            handled += 1
        return handled

    async def work_once(self) -> bool:
        """Claim and handle one due job; False when none is due."""
        job = await self._jobs.claim(self._clock(), LEASE)
        if job is None:
            return False
        try:
            await self._handle(job)
        except CoverLookupError as e:
            await self._retry_or_give_up(job, f"{e}")  # a CoverLookupError has no user data
        except Exception as e:  # one bad job must never stop the worker
            logger.error(
                "cover job failed unexpectedly (profile %s, book %d): %s\n%s",
                ref(job.profile_id), job.book_index, type(e).__name__, frames(e),
            )  # fmt: skip
            await self._retry_or_give_up(job, type(e).__name__)
        return True

    async def _run(self) -> None:
        while True:
            await self.run_until_idle()
            self._wake.clear()
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(self._idle_poll_s):
                    await self._wake.wait()

    async def _handle(self, job: CoverJob) -> None:
        stored = await self._store.get(job.profile_id, self._clock())
        if stored is None or job.book_index >= len(stored.profile.books):
            await self._jobs.complete(job.id)  # deleted or expired: don't spend a request
            return
        book = stored.profile.books[job.book_index]
        url = await cached_url(self._cache, book, self._clock())  # another profile found it
        if url is None and await _needs_lookup(self._cache, book, self._clock()):
            url = await _look_up(self._finder, self._cache, self._pacer, book, self._clock)
        if url is not None:  # one row, not the whole profile; a deleted one stays deleted
            await self._store.add_cover(job.profile_id, job.book_index, url)
        await self._jobs.complete(job.id)

    async def _retry_or_give_up(self, job: CoverJob, reason: str) -> None:
        retry_at = self._clock() + retry_delay(job.attempts)
        retrying = await self._jobs.fail(job.id, retry_at, MAX_ATTEMPTS)
        logger.warning(
            "cover lookup failed (profile %s, book %d, attempt %d): %s; %s",
            ref(job.profile_id), job.book_index, job.attempts, reason,
            "will retry" if retrying else "giving up",
        )  # fmt: skip


@dataclass
class CoverReport:
    covers: dict[int, str]
    looked_up: int = 0
    not_found: int = 0
    failed: int = 0


async def resolve_covers(
    profile: Profile,
    finder: CoverFinder,
    cache: CoverCache,
    *,
    pacer: Pacer | None = None,
    clock: Clock,
    on_progress: Callable[[int, int], None] = lambda done, total: None,
) -> CoverReport:
    """Every book's cover, in priority order, for the owner export. Cache hits are free;
    a failed lookup is counted and skipped, so a rerun retries only those."""
    pacer = pacer or Pacer()
    report = CoverReport(covers=await known_covers(profile, {}, cache, clock()))
    todo = [
        i
        for i in in_priority_order(profile)
        if i not in report.covers and await _needs_lookup(cache, profile.books[i], clock())
    ]
    for done, index in enumerate(todo, start=1):
        try:
            url = await _look_up(finder, cache, pacer, profile.books[index], clock)
        except CoverLookupError:
            report.failed += 1
        else:
            report.looked_up += 1
            if url is None:
                report.not_found += 1
            else:
                report.covers[index] = url
        on_progress(done, len(todo))
    return report


def _google_cover(book: Book, results: list[CatalogBook], *, by_isbn: bool) -> str | None:
    """The first result with a cover that is this book: any result of an ISBN search,
    else the same work, preferring a matching title (as with Open Library)."""
    if by_isbn:
        return next((str(r.cover_url) for r in results if r.cover_url), None)
    same = [
        r
        for r in results
        if r.cover_url and same_work(book.title, [book.author], r.title, r.authors)
    ]
    titled = [r for r in same if titles_match(book.title, r.title)]
    best = (titled or same)[:1]
    return str(best[0].cover_url) if best else None


async def google_covers(
    profile: Profile,
    covers: dict[int, str],
    books: BookSearch,
    cache: CoverCache,
    *,
    pacer: Pacer | None = None,
    clock: Clock,
    on_progress: Callable[[int, int], None] = lambda done, total: None,
) -> CoverReport:
    """The owner export's fallback: Google Books covers for the books still without one
    (D-046). One request per book (by ISBN when there is one, else by title and author),
    cached under google_cover_key so a rerun spends no quota. Never used for uploads."""
    pacer = pacer or Pacer(GOOGLE_BOOKS_INTERVAL_S)
    report = CoverReport(covers=dict(covers))
    todo: list[int] = []
    for index in in_priority_order(profile):
        if index in report.covers:
            continue
        cached = await cache.get_cover(google_cover_key(profile.books[index]))
        if cached is not None and cached.fresh(clock()):
            if cached.url:
                report.covers[index] = cached.url
            continue
        todo.append(index)
    for done, index in enumerate(todo, start=1):
        book = profile.books[index]
        isbn = book_isbn13(book)
        await pacer.wait()
        try:
            if isbn:
                results = await books.search(f"isbn:{isbn}", max_results=1)
            else:
                results = await books.find(
                    book.title, book.author, max_results=GOOGLE_BOOKS_RESULTS
                )
        except BookSearchError:
            pacer.failed()
            report.failed += 1
        else:
            pacer.succeeded()
            report.looked_up += 1
            url = _google_cover(book, results, by_isbn=bool(isbn))
            await cache.put_cover(google_cover_key(book), CachedCover(url, clock()))
            if url is None:
                report.not_found += 1
            else:
                report.covers[index] = url
        on_progress(done, len(todo))
    return report
