"""Book covers for the demo by Open Library cover id, never by ISBN URL (D-046, F-023).

Open Library's Covers API limits lookups by ISBN to 100 per IP every 5 minutes, so a
library of 200 books loaded in a browser loses most of its covers to 403s. Lookups by
cover id aren't limited, so each book is resolved to a cover id once, on the server:
- with an ISBN, from that exact edition's record, so the cover is the edition read;
- otherwise (or when that edition has no cover) through search by title and author,
  preferring an edition whose title matches, so a translation isn't picked.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from book_agent.domain.isbn import preferred_isbn13
from book_agent.domain.matching import normalize_author, normalize_title
from book_agent.domain.models import Book, ReadingStatus

COVER_ID_URL = "https://covers.openlibrary.org/b/id/{id}-M.jpg"
# A book without a cover may get one later: look again after a month. A found one is kept.
NOT_FOUND_MAX_AGE = timedelta(days=30)
# The library view opens on the read shelf, so its covers come first (lower is sooner).
SHELF_PRIORITY: dict[ReadingStatus, int] = {
    ReadingStatus.READ: 0,
    ReadingStatus.CURRENTLY_READING: 1,
    ReadingStatus.TO_READ: 2,
}


def book_isbn13(book: Book) -> str | None:
    return preferred_isbn13(book.isbn13, book.isbn)


def cover_key(book: Book) -> str:
    """The cache key: the ISBN-13 when there is one (a cover per edition), else the
    normalized title and author. normalize_title drops bracketed text, so "Guards! Guards!
    (Discworld #8)" and "Guards! Guards!" share one entry."""
    isbn = book_isbn13(book)
    if isbn:
        return f"isbn:{isbn}"
    return f"title:{normalize_title(book.title)}|{normalize_author(book.author)}"


def google_cover_key(book: Book) -> str:
    """The cache key of the owner export's Google Books fallback, kept apart so uploads
    never use it (they never spend Google Books quota)."""
    return f"google:{cover_key(book)}"


def cover_id_url(cover_id: int) -> str:
    return COVER_ID_URL.format(id=cover_id)


@dataclass(frozen=True)
class CachedCover:
    """A finished lookup: the cover URL, or None when no cover was found."""

    url: str | None
    checked_at: datetime

    def fresh(self, now: datetime) -> bool:
        return self.url is not None or now - self.checked_at < NOT_FOUND_MAX_AGE
