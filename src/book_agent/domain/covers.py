"""Book covers for the demo by Open Library cover id, never by ISBN (D-046, F-023).

Open Library's Covers API limits lookups by ISBN to 100 per IP every 5 minutes, so a
library of 200 books loaded in a browser loses most of its covers to 403s. Lookups by
cover id aren't limited, so covers are resolved to ids once (title and author through
Open Library search) and shown by id.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from book_agent.domain.matching import normalize_author, normalize_title
from book_agent.domain.models import ReadingStatus

COVER_ID_URL = "https://covers.openlibrary.org/b/id/{id}-M.jpg"
# A book without a cover may get one later: look again after a month. A found id is kept.
NOT_FOUND_MAX_AGE = timedelta(days=30)
# The library view opens on the read shelf, so its covers come first (lower is sooner).
SHELF_PRIORITY: dict[ReadingStatus, int] = {
    ReadingStatus.READ: 0,
    ReadingStatus.CURRENTLY_READING: 1,
    ReadingStatus.TO_READ: 2,
}


def cover_key(title: str, author: str) -> str:
    """The cache key: normalized title and author. normalize_title drops bracketed text,
    so "Guards! Guards! (Discworld #8)" and "Guards! Guards!" share one entry."""
    return f"{normalize_title(title)}|{normalize_author(author)}"


def cover_id_url(cover_id: int) -> str:
    return COVER_ID_URL.format(id=cover_id)


@dataclass(frozen=True)
class CachedCover:
    """A finished lookup: the cover id, or None when Open Library had no matching cover."""

    cover_id: int | None
    checked_at: datetime

    def fresh(self, now: datetime) -> bool:
        return self.cover_id is not None or now - self.checked_at < NOT_FOUND_MAX_AGE
