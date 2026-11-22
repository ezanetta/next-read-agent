"""Matching books by title and author when no shared id exists.

The model returns free-text titles; the library and the catalog have their own spellings
("Dune (Dune Chronicles, #1)", "Mistborn: The Final Empire", "The Final Empire"). These
pure functions decide when two of them are the same book.

Known limitation: translated or regional titles ("Philosopher's Stone" vs "Sorcerer's
Stone", "Sobre la brevedad de la vida" vs "De la brevedad de la vida") never match here:
the same-work judge handles them for the library check, never for existence (D-029).
"""

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

_BRACKETED = re.compile(r"\([^)]*\)|\[[^\]]*\]")  # Goodreads series markers live here
_APOSTROPHE = "['\u2019\u02bc`]"  # straight, typographic, modifier
_POSSESSIVE = re.compile(_APOSTROPHE + r"s\b")  # "Mistborn's" -> "Mistborn"
_APOSTROPHES = re.compile(_APOSTROPHE)
_NON_WORD = re.compile(r"[\W_]+")
_LEADING_ARTICLES = frozenset({"the", "a", "an", "el", "la", "los", "las", "un", "una"})
_MIN_CITATION_LENGTH = 4  # shorter single-word titles ("It") would match ordinary words
# Main title / subtitle: the first ":" or a spaced dash ("Title - Subtitle"). Not ". ",
# which would split "Mr. Mercedes" (D-029).
_SUBTITLE_SEPARATOR = re.compile(r":|\s+-\s+")
# Parts of a combined volume ("A - B", "A. B", "A; B", "A / B"), compared whole (D-029).
_VOLUME_SEPARATOR = re.compile(r"\s+-\s+|[.;]\s+|\s+/\s+")
_MIN_PART_LENGTH = 4  # a one-word part shorter than this is an abbreviation ("Mr", "Dr")


@dataclass(frozen=True)
class TitleVariants:
    """A normalized title split at the first ":" or " - ". `subtitle` is None without one."""

    full: str
    main: str
    subtitle: str | None


def _clean(text: str) -> str:
    """Accents removed, casefolded, & as "and", possessive 's and apostrophes dropped,
    punctuation as spaces."""
    decomposed = unicodedata.normalize("NFKD", text)
    without_marks = "".join(c for c in decomposed if not unicodedata.combining(c))
    folded = without_marks.casefold().replace("&", " and ")
    folded = _APOSTROPHES.sub("", _POSSESSIVE.sub("", folded))
    return " ".join(_NON_WORD.sub(" ", folded).split())


def _drop_leading_article(text: str) -> str:
    first, _, rest = text.partition(" ")
    return rest if first in _LEADING_ARTICLES and rest else text


def normalize_title(title: str) -> str:
    """Comparable form of a whole title: brackets removed, cleaned, leading article dropped."""
    return _drop_leading_article(_clean(_BRACKETED.sub(" ", title)))


def title_variants(title: str) -> TitleVariants:
    without_brackets = _BRACKETED.sub(" ", title)
    parts = _SUBTITLE_SEPARATOR.split(without_brackets, maxsplit=1)
    main, subtitle = parts[0], parts[1] if len(parts) == 2 else None
    return TitleVariants(
        full=normalize_title(without_brackets),
        main=normalize_title(main),
        subtitle=(normalize_title(subtitle) or None) if subtitle is not None else None,
    )


def volume_parts(title: str) -> list[str]:
    """The normalized titles inside a combined volume, or [] if it isn't one.

    "Sobre la felicidad. Sobre la brevedad de la vida" gives both titles. One-word parts
    shorter than 4 letters are dropped as abbreviations, and a title needs at least two
    parts left to count as a volume.
    """
    raw = _VOLUME_SEPARATOR.split(_BRACKETED.sub(" ", title))
    parts = [p for p in map(normalize_title, raw) if len(p) >= _MIN_PART_LENGTH or " " in p]
    return parts if len(parts) >= 2 else []


def titles_match(a: str, b: str) -> bool:
    """True when two titles name the same book.

    Matches when the full titles are equal, when one side's main title (before ":" or
    " - ") equals the other's full title ("Sapiens" vs "Sapiens: A Brief History of
    Humankind"), when one side's subtitle equals the other's full title ("Mistborn: The
    Final Empire" vs "The Final Empire"), or when one side is a combined volume with a
    part equal to the other's full title ("Sobre la felicidad. Sobre la brevedad de la
    vida" contains "Sobre la brevedad de la vida", D-029). Two titles never match just
    because their main titles are equal while their subtitles differ: those are different
    books in a series.
    """
    va, vb = title_variants(a), title_variants(b)
    if not va.full or not vb.full:
        return False
    if va.full == vb.full:
        return True
    for one, other in ((va, vb), (vb, va)):
        if one.subtitle is not None and other.full in (one.main, one.subtitle):
            return True
    return va.full in volume_parts(b) or vb.full in volume_parts(a)


def normalize_author(author: str) -> str:
    return _clean(author)


def author_surname(author: str) -> str:
    """The last word of a normalized name: "J.R.R. Tolkien" and "J. R. R. Tolkien" -> "tolkien"."""
    words = normalize_author(author).split()
    return words[-1] if words else ""


def authors_match(a: Sequence[str], b: Sequence[str]) -> bool:
    """True when any author on one side shares a surname with any author on the other."""
    surnames_a = {s for s in map(author_surname, a) if s}
    return any(author_surname(name) in surnames_a for name in b)


def same_work(
    title_a: str, authors_a: Sequence[str], title_b: str, authors_b: Sequence[str]
) -> bool:
    return titles_match(title_a, title_b) and authors_match(authors_a, authors_b)


def mentions_title(text: str, title: str) -> bool:
    """True when `text` mentions the title (full, main or subtitle) as whole words."""
    haystack = f" {_clean(text)} "
    variants = title_variants(title)
    candidates = {variants.full, variants.main, variants.subtitle or ""}
    return any(
        f" {c} " in haystack for c in candidates if len(c) >= _MIN_CITATION_LENGTH or " " in c
    )
