"""ISBN normalization: every comparison and cache key uses the ISBN-13 form."""

import re

_SEPARATORS = re.compile(r"[\s-]")
_ISBN10 = re.compile(r"^\d{9}[\dX]$")
_ISBN13 = re.compile(r"^97[89]\d{10}$")


def to_isbn13(value: str | None) -> str | None:
    """Clean an ISBN-10 or ISBN-13 and return it as an ISBN-13.

    Hyphens, spaces and a lower-case "x" are accepted. Returns None for anything that
    is not a valid ISBN, including a wrong check digit, so a typo never matches a real book.
    """
    if not value:
        return None
    isbn = _SEPARATORS.sub("", value).upper()
    if _ISBN13.match(isbn):
        return isbn if _isbn13_check_digit(isbn[:12]) == isbn[12] else None
    if _ISBN10.match(isbn) and _isbn10_is_valid(isbn):
        stem = "978" + isbn[:9]
        return stem + _isbn13_check_digit(stem)
    return None


def preferred_isbn13(isbn13: str | None, isbn10: str | None) -> str | None:
    """One ISBN-13 for a record with both columns, preferring the ISBN-13 column."""
    return to_isbn13(isbn13) or to_isbn13(isbn10)


def _isbn10_is_valid(isbn: str) -> bool:
    """Weights 10 down to 1; "X" is worth 10; the sum must be divisible by 11."""
    if "X" in isbn[:9]:
        return False
    values = [10 if c == "X" else int(c) for c in isbn]
    return sum(v * w for v, w in zip(values, range(10, 0, -1), strict=True)) % 11 == 0


def _isbn13_check_digit(stem: str) -> str:
    """Alternating weights 1 and 3 over the first 12 digits."""
    total = sum(int(c) * (3 if i % 2 else 1) for i, c in enumerate(stem))
    return str((10 - total % 10) % 10)
