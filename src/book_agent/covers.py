"""Resolving covers to Open Library cover ids, politely (D-046).

- `CoverQueue`: one per server. Uploaded profiles are saved first, then their books
  without a cover wait here; a single worker looks them up one request per second, read
  books first, and saves each cover id URL on the profile as it's found. Concurrent
  uploads share the queue, so they never raise the request rate.
- `resolve_covers`: the same lookups for a whole profile in one go, for the owner export,
  and `google_covers`: the export's fallback for books Open Library has no cover for.

A lookup is the edition record by ISBN, then (no ISBN, or no cover there) a search by
title and author: up to two requests, each paced. Both check the shared cache first, by
ISBN or by normalized title and author, so a book any profile already resolved costs no
request.
"""

import asyncio
import logging
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime

from book_agent.domain.covers import (
    SHELF_PRIORITY,
    CachedCover,
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


@dataclass(order=True, frozen=True)
class _Item:
    priority: int
    seq: int
    profile_id: str = field(compare=False)
    index: int = field(compare=False)
    book: Book = field(compare=False)


class CoverQueue:
    """The server's single queue of cover lookups.

    The worker starts with the first submission (in the server's event loop) and is
    cancelled by `aclose`. Pending lookups live in memory: a restart drops them, and those
    books keep the placeholder (D-046).
    """

    def __init__(
        self,
        finder: CoverFinder,
        cache: CoverCache,
        store: ProfileStore,
        *,
        pacer: Pacer | None = None,
        clock: Clock,
    ) -> None:
        self._finder = finder
        self._cache = cache
        self._store = store
        self._pacer = pacer or Pacer()
        self._clock = clock
        self._queue: asyncio.PriorityQueue[_Item] | None = None
        self._worker: asyncio.Task[None] | None = None
        self._pending: Counter[str] = Counter()
        self._seq = 0

    async def known(self, profile: Profile, covers: dict[int, str]) -> dict[int, str]:
        return await known_covers(profile, covers, self._cache, self._clock())

    async def submit(self, stored: StoredProfile) -> int:
        """Queue the books with no cover that the cache can't answer; returns how many."""
        now = self._clock()
        queued = 0
        for index in in_priority_order(stored.profile):
            book = stored.profile.books[index]
            if index in stored.covers or not await _needs_lookup(self._cache, book, now):
                continue
            self._seq += 1
            self._ensure_worker().put_nowait(
                _Item(SHELF_PRIORITY[book.status], self._seq, stored.id, index, book)
            )
            queued += 1
        self._pending[stored.id] += queued
        return queued

    def pending(self, profile_id: str) -> int:
        """Lookups still waiting for this profile; 0 when its covers are done."""
        return self._pending[profile_id]

    async def join(self) -> None:
        """Wait until every queued lookup is done (tests and shutdown)."""
        if self._queue is not None:
            await self._queue.join()

    async def aclose(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
            self._worker = None

    def _ensure_worker(self) -> asyncio.PriorityQueue[_Item]:
        if self._queue is None:
            self._queue = asyncio.PriorityQueue()
        if self._worker is None or self._worker.done():
            self._worker = asyncio.get_running_loop().create_task(self._run())
        return self._queue

    async def _run(self) -> None:
        assert self._queue is not None
        while True:
            item = await self._queue.get()
            try:
                await self._process(item)
            except Exception as e:  # one bad book must never stop the worker
                logger.error(
                    "cover lookup failed unexpectedly (profile %s, book %d): %s\n%s",
                    ref(item.profile_id), item.index, type(e).__name__, frames(e),
                )  # fmt: skip
            finally:
                self._pending[item.profile_id] -= 1
                if self._pending[item.profile_id] <= 0:
                    del self._pending[item.profile_id]
                self._queue.task_done()

    async def _process(self, item: _Item) -> None:
        if await self._store.get(item.profile_id, self._clock()) is None:
            return  # deleted or expired while waiting: don't spend a request
        url = await cached_url(self._cache, item.book, self._clock())  # another profile found it
        if url is None and await _needs_lookup(self._cache, item.book, self._clock()):
            try:
                url = await _look_up(self._finder, self._cache, self._pacer, item.book, self._clock)
            except CoverLookupError as e:
                logger.warning(
                    "cover lookup failed (profile %s, book %d): %s",
                    ref(item.profile_id), item.index, e,  # a CoverLookupError has no user data
                )  # fmt: skip
                return
        if url is not None:  # one row, not the whole profile; a deleted one stays deleted
            await self._store.add_cover(item.profile_id, item.index, url)


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
