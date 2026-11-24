"""Describing the reader's own books once: description and subjects, cached (D-041).

The Goodreads export has titles and ratings but no descriptions, so retrieval has nothing
to compare a request with. Each book is described once, from the cheapest source first:
1. Open Library: the work's description and subjects, through the cached work key of the
   book's ISBN (work_keys.py). Free; needs OPEN_LIBRARY_CONTACT.
2. Google Books: the first edition matching title and author that has a description.
   Uses the daily quota shared with eval runs, so only books Open Library left without a
   description get here.

The cache is keyed by book (book_key) and saved as it goes: a stop or a quota error keeps
every book already described, and a rerun continues. A failed lookup isn't cached, so it
is retried; a book no source describes is cached as such, so it isn't.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError

from book_agent.domain.isbn import preferred_isbn13
from book_agent.domain.matching import normalize_author, normalize_title, same_work
from book_agent.domain.models import Book
from book_agent.files import write_atomic
from book_agent.ports import BookSearch, BookSearchError, WorkDescriptions, WorkLookupError
from book_agent.work_keys import WorkKeyCache

DEFAULT_ENRICHMENT_PATH = Path("data/library_books.json")
SEARCH_RESULTS = 5
MAX_SUBJECTS = 15

Source = Literal["open_library", "google_books", "none"]


class LibraryEntry(BaseModel, frozen=True):
    title: str
    author: str
    description: str | None = None
    subjects: tuple[str, ...] = ()
    source: Source
    checked: date


class EnrichmentCache(BaseModel):
    version: Literal[1] = 1
    entries: dict[str, LibraryEntry] = {}


def book_key(book: Book) -> str:
    """A stable key for a library book: its Goodreads id, or its normalized title and author."""
    if book.goodreads_id:
        return f"goodreads:{book.goodreads_id}"
    return f"title:{normalize_title(book.title)}|{normalize_author(book.author)}"


def load_enrichment(path: Path) -> EnrichmentCache:
    """The cache at `path`, or an empty one if the file doesn't exist. Raises ValueError
    for an invalid file rather than silently starting over."""
    if not path.exists():
        return EnrichmentCache()
    try:
        return EnrichmentCache.model_validate_json(path.read_bytes())
    except ValidationError as e:
        raise ValueError(f"{path} is not a valid enrichment cache: {e.error_count()} errors") from e


def save_enrichment(cache: EnrichmentCache, path: Path) -> None:
    write_atomic(path, cache.model_dump_json(indent=1) + "\n")


def to_enrich(
    books: Sequence[Book], cache: EnrichmentCache, *, refresh: bool = False
) -> list[Book]:
    """The books without a cache entry (all of them with `refresh`), without duplicates."""
    seen: set[str] = set()
    todo: list[Book] = []
    for book in books:
        key = book_key(book)
        if key in seen or (key in cache.entries and not refresh):
            continue
        seen.add(key)
        todo.append(book)
    return todo


@dataclass
class EnrichmentSummary:
    open_library: int = 0
    google_books: int = 0
    undescribed: int = 0  # no source has a description: cached, not retried
    failed: int = 0  # a lookup failed: not cached, retried on the next run
    search_requests: int = 0  # Google Books searches sent


async def enrich_books(
    books: Sequence[Book],
    cache: EnrichmentCache,
    *,
    work_keys: WorkKeyCache,
    works: WorkDescriptions | None,
    search: BookSearch,
    today: date,
    checkpoint: Callable[[EnrichmentCache], None] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> EnrichmentSummary:
    """Describe each book in `books` and store it in `cache`, one at a time.

    `works` is None when Open Library is off (no contact set): every book goes to Google
    Books. `checkpoint` is called after each new entry, to save the cache.
    """
    summary = EnrichmentSummary()
    for number, book in enumerate(books, start=1):
        entry = await _describe(book, work_keys, works, search, today, summary)
        if entry is not None:
            cache.entries[book_key(book)] = entry
            if checkpoint is not None:
                checkpoint(cache)
        if progress is not None:
            progress(number, len(books))
    return summary


async def _describe(
    book: Book,
    work_keys: WorkKeyCache,
    works: WorkDescriptions | None,
    search: BookSearch,
    today: date,
    summary: EnrichmentSummary,
) -> LibraryEntry | None:
    subjects: tuple[str, ...] = ()
    lookup_failed = False
    work_key = _work_key(book, work_keys)
    if works is not None and work_key is not None:
        try:
            info = await works.work_info(work_key)
        except WorkLookupError:
            info, lookup_failed = None, True
        if info is not None:
            subjects = info.subjects
            if info.description:
                summary.open_library += 1
                return _entry(book, info.description, subjects, "open_library", today)

    summary.search_requests += 1
    try:
        found = await search.find(book.title, book.author, max_results=SEARCH_RESULTS)
    except BookSearchError:
        summary.failed += 1
        return None
    for candidate in found:
        if candidate.description and same_work(
            book.title, [book.author], candidate.title, candidate.authors
        ):
            summary.google_books += 1
            merged = subjects or candidate.categories
            return _entry(book, candidate.description, merged, "google_books", today)
    if lookup_failed:  # Open Library may still have it: retry next run
        summary.failed += 1
        return None
    summary.undescribed += 1
    return _entry(book, None, subjects, "none", today)


def _work_key(book: Book, work_keys: WorkKeyCache) -> str | None:
    isbn = preferred_isbn13(book.isbn13, book.isbn)
    entry = work_keys.entries.get(isbn) if isbn else None
    return entry.work if entry is not None else None


def _entry(
    book: Book, description: str | None, subjects: Sequence[str], source: Source, today: date
) -> LibraryEntry:
    return LibraryEntry(
        title=book.title,
        author=book.author,
        description=" ".join(description.split()) if description else None,
        subjects=tuple(subjects[:MAX_SUBJECTS]),
        source=source,
        checked=today,
    )
