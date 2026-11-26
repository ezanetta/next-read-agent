"""Resolving covers to Open Library cover ids, politely (D-046).

- `CoverQueue`: one per server. Uploaded profiles are saved first, then their books
  without a cover wait here; a single worker looks them up one request per second, read
  books first, and saves each cover id URL on the profile as it's found. Concurrent
  uploads share the queue, so they never raise the request rate.
- `resolve_covers`: the same lookups for a whole profile in one go, for the owner export.

Both check the shared cache by normalized title and author first: a book any profile
already resolved costs no request.
"""

import asyncio
import logging
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime

from book_agent.domain.covers import SHELF_PRIORITY, CachedCover, cover_id_url, cover_key
from book_agent.domain.models import Book, Profile
from book_agent.domain.profiles import StoredProfile
from book_agent.ports import CoverCache, CoverFinder, CoverLookupError, ProfileStore

logger = logging.getLogger(__name__)

REQUEST_INTERVAL_S = 1.0
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


def cached_url(cache: CoverCache, book: Book, now: datetime) -> str | None:
    """The cover URL when the cache already answers for this book, else None."""
    cached = cache.get_cover(cover_key(book.title, book.author))
    if cached is not None and cached.cover_id is not None and cached.fresh(now):
        return cover_id_url(cached.cover_id)
    return None


def _needs_lookup(cache: CoverCache, book: Book, now: datetime) -> bool:
    cached = cache.get_cover(cover_key(book.title, book.author))
    return cached is None or not cached.fresh(now)


async def _look_up(
    finder: CoverFinder, cache: CoverCache, pacer: Pacer, book: Book, now: Clock
) -> int | None:
    """One paced lookup, cached when it finishes. Raises CoverLookupError (not cached)."""
    await pacer.wait()
    try:
        cover_id = await finder.cover_id(book.title, book.author)
    except CoverLookupError:
        pacer.failed()
        raise
    pacer.succeeded()
    cache.put_cover(cover_key(book.title, book.author), CachedCover(cover_id, now()))
    return cover_id


def in_priority_order(profile: Profile) -> list[int]:
    """Book indexes: read first, then currently reading, then to-read; library order within."""
    books = profile.books
    return sorted(range(len(books)), key=lambda i: SHELF_PRIORITY[books[i].status])


def known_covers(
    profile: Profile, covers: dict[int, str], cache: CoverCache, now: datetime
) -> dict[int, str]:
    """`covers` plus every cover the cache already has. Makes no requests."""
    found = dict(covers)
    for index, book in enumerate(profile.books):
        if index not in found and (url := cached_url(cache, book, now)):
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

    def known(self, profile: Profile, covers: dict[int, str]) -> dict[int, str]:
        return known_covers(profile, covers, self._cache, self._clock())

    def submit(self, stored: StoredProfile) -> int:
        """Queue the books with no cover that the cache can't answer; returns how many."""
        now = self._clock()
        queued = 0
        for index in in_priority_order(stored.profile):
            book = stored.profile.books[index]
            if index in stored.covers or not _needs_lookup(self._cache, book, now):
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
            except Exception:  # one bad book must never stop the worker
                logger.exception("cover lookup for %r failed", item.book.title)
            finally:
                self._pending[item.profile_id] -= 1
                if self._pending[item.profile_id] <= 0:
                    del self._pending[item.profile_id]
                self._queue.task_done()

    async def _process(self, item: _Item) -> None:
        if self._store.get(item.profile_id, self._clock()) is None:
            return  # deleted or expired while waiting: don't spend a request
        url = cached_url(self._cache, item.book, self._clock())  # another profile found it
        if url is None and _needs_lookup(self._cache, item.book, self._clock()):
            try:
                cover_id = await _look_up(
                    self._finder, self._cache, self._pacer, item.book, self._clock
                )
            except CoverLookupError as e:
                logger.warning("cover lookup for %r failed: %s", item.book.title, e)
                return
            url = cover_id_url(cover_id) if cover_id is not None else None
        if url is None:
            return
        # Read again after the await: save onto the latest version of the profile.
        stored = self._store.get(item.profile_id, self._clock())
        if stored is not None:
            self._store.save(
                stored.model_copy(update={"covers": {**stored.covers, item.index: url}})
            )


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
    report = CoverReport(covers=known_covers(profile, {}, cache, clock()))
    todo = [
        i
        for i in in_priority_order(profile)
        if i not in report.covers and _needs_lookup(cache, profile.books[i], clock())
    ]
    for done, index in enumerate(todo, start=1):
        try:
            cover_id = await _look_up(finder, cache, pacer, profile.books[index], clock)
        except CoverLookupError:
            report.failed += 1
        else:
            report.looked_up += 1
            if cover_id is None:
                report.not_found += 1
            else:
                report.covers[index] = cover_id_url(cover_id)
        on_progress(done, len(todo))
    return report
