"""A local cache of ISBN -> work key lookups, and the library coverage it gives.

The library is resolved once with the `works` command, not on every run. The cache is
keyed by ISBN-13, so it serves library books and recommendations alike and survives a
new Goodreads export. Failed lookups are not cached, so the next run retries them.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError

from book_agent.domain.isbn import preferred_isbn13
from book_agent.domain.models import Profile
from book_agent.files import write_atomic
from book_agent.ports import WorkLookupError, WorkResolver

DEFAULT_CACHE_PATH = Path("data/work_keys.json")
DEFAULT_CHECKPOINT_EVERY = 20  # lookups between cache saves while resolving the library


class CacheEntry(BaseModel, frozen=True):
    work: str | None  # None: the catalog does not know this ISBN
    checked: date


class WorkKeyCache(BaseModel):
    version: Literal[1] = 1
    entries: dict[str, CacheEntry] = {}


Progress = Callable[[int, int], None]
"""Called with (done, total) after each library ISBN is looked up."""

Checkpoint = Callable[[WorkKeyCache], None]
"""Called with the cache every few lookups, to save it so an interrupted run keeps its work."""


def load_cache(path: Path) -> WorkKeyCache:
    """The cache stored at `path`, or an empty one if the file does not exist.

    Raises ValueError if the file is not a valid cache, rather than silently starting over.
    """
    if not path.exists():
        return WorkKeyCache()
    try:
        return WorkKeyCache.model_validate_json(path.read_bytes())
    except ValidationError as e:
        raise ValueError(f"{path} is not a valid work key cache: {e.error_count()} errors") from e


def save_cache(cache: WorkKeyCache, path: Path) -> None:
    """Write the cache atomically: a crash mid-write never leaves a broken file."""
    write_atomic(path, cache.model_dump_json(indent=1) + "\n")


class CachedWorkResolver:
    """WorkResolver that answers from the cache and asks `inner` only for new ISBNs.

    With refresh=True every ISBN is asked again once and the entry overwritten; a failed
    lookup keeps the old entry. `writes` counts new or updated entries, and `changed`
    tells the caller whether to save the cache.
    """

    def __init__(
        self,
        inner: WorkResolver,
        cache: WorkKeyCache,
        *,
        refresh: bool = False,
        today: Callable[[], date] = date.today,
    ) -> None:
        self._inner = inner
        self.cache = cache
        self._refresh = refresh
        self._refreshed: set[str] = set()
        self._today = today
        self.writes = 0

    async def work_for_isbn(self, isbn13: str) -> str | None:
        entry = self.cache.entries.get(isbn13)
        if entry is not None and (not self._refresh or isbn13 in self._refreshed):
            return entry.work
        work = await self._inner.work_for_isbn(isbn13)  # WorkLookupError is not cached
        self.cache.entries[isbn13] = CacheEntry(work=work, checked=self._today())
        self._refreshed.add(isbn13)
        self.writes += 1
        return work

    @property
    def changed(self) -> bool:
        return self.writes > 0


@dataclass(frozen=True)
class Coverage:
    books: int
    with_isbn: int
    resolved: int  # mapped to a work
    unknown: int  # the catalog does not know the ISBN
    unchecked: int  # not in the cache yet, or the lookup failed

    @property
    def without_isbn(self) -> int:
        return self.books - self.with_isbn

    @property
    def complete(self) -> bool:
        return self.unchecked == 0


def coverage(profile: Profile, cache: WorkKeyCache) -> Coverage:
    """How much of the library the cache covers. Makes no requests."""
    isbns = [isbn for b in profile.books if (isbn := preferred_isbn13(b.isbn13, b.isbn))]
    entries = [cache.entries.get(isbn) for isbn in isbns]
    return Coverage(
        books=len(profile.books),
        with_isbn=len(isbns),
        resolved=sum(1 for e in entries if e is not None and e.work is not None),
        unknown=sum(1 for e in entries if e is not None and e.work is None),
        unchecked=sum(1 for e in entries if e is None),
    )


async def resolve_library(
    profile: Profile,
    resolver: CachedWorkResolver,
    *,
    progress: Progress | None = None,
    checkpoint: Checkpoint | None = None,
    checkpoint_every: int = DEFAULT_CHECKPOINT_EVERY,
) -> tuple[Coverage, int]:
    """Look up every library ISBN not yet cached (all of them with refresh).

    Returns the coverage and the number of failed lookups. Failures are not cached, so
    a rerun retries them; during a refresh a failure keeps the old entry.
    `progress` hears about every lookup, cached ones included. `checkpoint` is called
    every `checkpoint_every` lookups when the cache has changed since the last call;
    saving at the end is left to the caller.
    """
    isbns = sorted({isbn for b in profile.books if (isbn := preferred_isbn13(b.isbn13, b.isbn))})
    done = 0
    saved_writes = resolver.writes

    async def failed(isbn: str) -> bool:
        nonlocal done, saved_writes
        try:
            await resolver.work_for_isbn(isbn)
            lookup_failed = False
        except WorkLookupError:
            lookup_failed = True
        # Only finished lookups count: a crash or Ctrl-C skips this. Lookups run
        # concurrently, but this block has no await, so the counters need no lock.
        done += 1
        if progress is not None:
            progress(done, len(isbns))
        due = done % checkpoint_every == 0
        if checkpoint is not None and due and resolver.writes > saved_writes:
            checkpoint(resolver.cache)
            saved_writes = resolver.writes
        return lookup_failed

    failures = await asyncio.gather(*(failed(isbn) for isbn in isbns))
    return coverage(profile, resolver.cache), sum(failures)


def format_coverage(result: Coverage, failed: int = 0) -> list[str]:
    lines = [
        f"Library: {result.books} books, {result.with_isbn} with an ISBN",
        f"  {result.resolved} resolved to a work, {result.unknown} unknown to Open Library, "
        f"{result.unchecked} not checked yet",
    ]
    if failed:
        lines.append(f"  {failed} lookups failed: run the works command again to retry")
    lines.append(f"  {result.without_isbn} without an ISBN are covered only by the LLM check")
    return lines
