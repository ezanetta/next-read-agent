"""Goodreads CSV export parser.

Turns the CSV produced by Goodreads "Export Library" into a Profile.
Invalid rows are skipped with a logged warning instead of failing the whole file.
"""

import csv
import logging
from collections.abc import Iterable, Mapping
from pathlib import Path

from book_agent.domain.models import Book, Profile, ReadingStatus

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = frozenset({"Book Id", "Title", "Author", "Exclusive Shelf"})

SHELF_TO_STATUS: Mapping[str, ReadingStatus] = {
    "read": ReadingStatus.READ,
    "to-read": ReadingStatus.TO_READ,
    "currently-reading": ReadingStatus.CURRENTLY_READING,
}


def load_goodreads_export(path: Path, profile_name: str) -> Profile:
    """Read a Goodreads export file from disk and parse it into a Profile."""
    # utf-8-sig strips a byte-order mark if present; newline="" is what csv expects.
    with path.open(encoding="utf-8-sig", newline="") as f:
        return parse_goodreads_csv(f, profile_name)


def parse_goodreads_csv(lines: Iterable[str], profile_name: str) -> Profile:
    """Parse Goodreads export CSV content into a Profile.

    Raises ValueError if the header is missing required columns, since then no row
    could be parsed. Individual bad rows are logged and skipped.
    """
    reader = csv.DictReader(lines)
    missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
    if missing:
        raise ValueError(f"Not a Goodreads export: missing columns {sorted(missing)}")

    books: list[Book] = []
    skipped = 0
    for row in reader:
        try:
            books.append(_row_to_book(row))
        except ValueError as e:  # pydantic.ValidationError is a ValueError too
            skipped += 1
            title = row.get("Title") or "<no title>"
            logger.warning("Skipping row %d (%r): %s", reader.line_num, title, e)

    if skipped:
        total = len(books) + skipped
        logger.warning("Skipped %d of %d rows in Goodreads export", skipped, total)

    return Profile(name=profile_name, books=books)


def _row_to_book(row: Mapping[str, str | None]) -> Book:
    def col(name: str) -> str:
        return (row.get(name) or "").strip()

    shelf = col("Exclusive Shelf")
    status = SHELF_TO_STATUS.get(shelf)
    if status is None:
        raise ValueError(f"unknown exclusive shelf {shelf!r}")

    rating = _optional_float(col("My Rating"))
    return Book(
        goodreads_id=col("Book Id") or None,
        title=col("Title"),
        author=col("Author"),
        isbn=_clean_isbn(col("ISBN")),
        isbn13=_clean_isbn(col("ISBN13")),
        my_rating=rating or None,  # Goodreads uses 0 (or 0.0) for "not rated"
        pages=_optional_int(col("Number of Pages")),
        year=_optional_int(col("Original Publication Year"))
        or _optional_int(col("Year Published")),
        status=status,
    )


def _clean_isbn(raw: str) -> str | None:
    """Unwrap Goodreads' Excel-style ="0123456789" ISBN cells. Empty becomes None."""
    cleaned = raw.removeprefix("=").strip('"').strip()
    return cleaned or None


def _optional_int(raw: str) -> int | None:
    """Parse an int, treating an empty cell as None. Raises ValueError on garbage."""
    return int(raw) if raw else None


def _optional_float(raw: str) -> float | None:
    """Parse a float, treating an empty cell as None. Raises ValueError on garbage."""
    return float(raw) if raw else None
