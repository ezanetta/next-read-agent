"""A local cache of ISBN -> work key lookups, and the library coverage it gives.

The library is resolved once with the `works` command, not on every run. The cache is
keyed by ISBN-13, so it serves library books and recommendations alike and survives a
new Goodreads export. Failed lookups are not cached, so the next run retries them.
"""

import asyncio
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError

from book_agent.domain.isbn import preferred_isbn13
from book_agent.domain.models import Profile
from book_agent.ports import WorkLookupError, WorkResolver

DEFAULT_CACHE_PATH = Path("data/work_keys.json")


class CacheEntry(BaseModel, frozen=True):
    work: str | None  # None: the catalog does not know this ISBN
    checked: date


class WorkKeyCache(BaseModel):
    version: Literal[1] = 1
    entries: dict[str, CacheEntry] = {}


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
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(cache.model_dump_json(indent=1) + "\n", encoding="utf-8")
    os.replace(temporary, path)


class CachedWorkResolver:
    """WorkResolver that answers from the cache and asks `inner` only for new ISBNs.

    With refresh=True every ISBN is asked again once and the entry overwritten; a failed
    lookup keeps the old entry. `changed` tells the caller whether to save the cache.
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
        self.changed = False

    async def work_for_isbn(self, isbn13: str) -> str | None:
        entry = self.cache.entries.get(isbn13)
        if entry is not None and (not self._refresh or isbn13 in self._refreshed):
            return entry.work
        work = await self._inner.work_for_isbn(isbn13)  # WorkLookupError is not cached
        self.cache.entries[isbn13] = CacheEntry(work=work, checked=self._today())
        self._refreshed.add(isbn13)
        self.changed = True
        return work


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


async def resolve_library(profile: Profile, resolver: CachedWorkResolver) -> tuple[Coverage, int]:
    """Look up every library ISBN not yet cached (all of them with refresh).

    Returns the coverage and the number of failed lookups. Failures are not cached, so
    a rerun retries them; during a refresh a failure keeps the old entry.
    """

    async def failed(isbn: str) -> bool:
        try:
            await resolver.work_for_isbn(isbn)
        except WorkLookupError:
            return True
        return False

    isbns = sorted({isbn for b in profile.books if (isbn := preferred_isbn13(b.isbn13, b.isbn))})
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
