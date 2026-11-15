"""Matching books by title and author when no shared id exists.

The model returns free-text titles; the library and the catalog have their own spellings
("Dune (Dune Chronicles, #1)", "Mistborn: The Final Empire", "The Final Empire"). These
pure functions decide when two of them are the same book.

Known limitation: translated or regional titles ("Philosopher's Stone" vs "Sorcerer's
Stone") never match. Grouping editions under one work is planned separately.
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


@dataclass(frozen=True)
class TitleVariants:
    """A normalized title split at the first colon. `subtitle` is None without a colon."""

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
    main, colon, subtitle = without_brackets.partition(":")
    return TitleVariants(
        full=normalize_title(without_brackets),
        main=normalize_title(main),
        subtitle=(normalize_title(subtitle) or None) if colon else None,
    )


def titles_match(a: str, b: str) -> bool:
    """True when two titles name the same book.

    Matches when the full titles are equal, when one side's main title (before ":")
    equals the other's full title ("Sapiens" vs "Sapiens: A Brief History of Humankind"),
    or when one side's subtitle equals the other's full title ("Mistborn: The Final
    Empire" vs "The Final Empire"). Two titles never match just because their main titles
    are equal while their subtitles differ: those are different books in a series.
    """
    va, vb = title_variants(a), title_variants(b)
    if not va.full or not vb.full:
        return False
    if va.full == vb.full:
        return True
    for one, other in ((va, vb), (vb, va)):
        if one.subtitle is not None and other.full in (one.main, one.subtitle):
            return True
    return False


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
