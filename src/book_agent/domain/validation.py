"""Hard-rule validation of recommendations, shared by the baseline, the agent and the evals.

Imports the BookSearch port, never an HTTP library or an LLM SDK.
"""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass

from book_agent.domain.matching import mentions_title, same_work
from book_agent.domain.models import Book, CatalogBook, Profile, ReadingStatus
from book_agent.domain.recommendations import (
    EXPECTED_RECOMMENDATIONS,
    Check,
    CheckedRecommendation,
    Filters,
    Recommendation,
    Rule,
    Unknown,
    ValidationResult,
    Violation,
)
from book_agent.ports import BookSearch, BookSearchError

_SEARCH_RESULTS = 5

_LIBRARY_RULES = {
    ReadingStatus.READ: (Rule.ALREADY_READ, "already read"),
    ReadingStatus.TO_READ: (Rule.ON_TO_READ, "already on the to-read list"),
    ReadingStatus.CURRENTLY_READING: (Rule.CURRENTLY_READING, "currently being read"),
}


@dataclass(frozen=True)
class _Resolution:
    match: CatalogBook | None
    lookup_error: str | None = None


async def validate_recommendations(
    recommendations: Sequence[Recommendation],
    profile: Profile,
    filters: Filters,
    books: BookSearch,
) -> ValidationResult:
    """Check every hard rule and report all violations and unknowns, not just pass/fail."""
    resolutions = await asyncio.gather(*(_resolve(r, filters, books) for r in recommendations))

    violations: list[Violation] = []
    unknowns: list[Unknown] = []
    if len(recommendations) != EXPECTED_RECOMMENDATIONS:
        violations.append(
            Violation(
                rule=Rule.WRONG_COUNT,
                index=None,
                message=f"expected {EXPECTED_RECOMMENDATIONS} books, got {len(recommendations)}",
            )
        )
    violations += _duplicates(recommendations, resolutions)

    items: list[CheckedRecommendation] = []
    for index, (rec, resolution) in enumerate(zip(recommendations, resolutions, strict=True)):
        item, item_violations, item_unknowns = _check_one(index, rec, resolution, profile, filters)
        items.append(item)
        violations += item_violations
        unknowns += item_unknowns

    return ValidationResult(items=items, violations=violations, unknowns=unknowns)


# --- Resolution ---------------------------------------------------------------


async def _resolve(rec: Recommendation, filters: Filters, books: BookSearch) -> _Resolution:
    """Find the catalog edition that best meets the filters.

    Searches with the language filter first; if no edition matches title and author,
    searches again without it, so a book that exists only in another language is
    reported as a language failure rather than as not found.
    """
    query = f"{rec.title} {rec.author}"
    try:
        matches: list[CatalogBook] = []
        if filters.language:
            matches = _matching(
                rec,
                await books.search(query, language=filters.language, max_results=_SEARCH_RESULTS),
            )
        if not matches:
            matches = _matching(rec, await books.search(query, max_results=_SEARCH_RESULTS))
    except BookSearchError as e:
        return _Resolution(match=None, lookup_error=str(e))
    if not matches:
        return _Resolution(match=None)
    # Best edition: fewest failed filters, then fewest unknowns; ties keep search order.
    return _Resolution(match=min(matches, key=lambda c: _filter_score(c, filters)))


def _matching(rec: Recommendation, candidates: list[CatalogBook]) -> list[CatalogBook]:
    return [c for c in candidates if same_work(rec.title, [rec.author], c.title, c.authors)]


def _filter_score(book: CatalogBook, filters: Filters) -> tuple[int, int]:
    checks = (_language_check(book, filters), _pages_check(book, filters))
    return (checks.count(Check.FAIL), checks.count(Check.UNKNOWN))


# --- Per-book checks ------------------------------------------------------------


def _check_one(
    index: int,
    rec: Recommendation,
    resolution: _Resolution,
    profile: Profile,
    filters: Filters,
) -> tuple[CheckedRecommendation, list[Violation], list[Unknown]]:
    violations: list[Violation] = []
    unknowns: list[Unknown] = []
    match = resolution.match
    label = f"{rec.title!r} by {rec.author}"

    if match is not None:
        exists = Check.PASS
    elif resolution.lookup_error is not None:
        exists = Check.UNKNOWN
        unknowns.append(
            Unknown(
                rule=Rule.NOT_FOUND,
                index=index,
                message=f"{label}: lookup failed ({resolution.lookup_error})",
            )
        )
    else:
        exists = Check.FAIL
        violations.append(
            Violation(
                rule=Rule.NOT_FOUND, index=index, message=f"{label}: not found in the catalog"
            )
        )

    library_match = _library_collision(rec, match, profile)
    if library_match is None:
        not_in_library = Check.PASS
    else:
        not_in_library = Check.FAIL
        rule, why = _LIBRARY_RULES[library_match.status]
        violations.append(
            Violation(
                rule=rule, index=index, message=f"{label}: {why} (as {library_match.title!r})"
            )
        )

    language = _language_check(match, filters) if match else Check.SKIPPED
    if language is Check.FAIL and match:
        violations.append(
            Violation(
                rule=Rule.LANGUAGE,
                index=index,
                message=f"{label}: language {match.language!r}, wanted {filters.language!r}",
            )
        )
    elif language is Check.UNKNOWN:
        unknowns.append(
            Unknown(rule=Rule.LANGUAGE, index=index, message=f"{label}: language unknown")
        )

    pages = _pages_check(match, filters) if match else Check.SKIPPED
    if pages is Check.FAIL and match:
        violations.append(
            Violation(
                rule=Rule.MAX_PAGES,
                index=index,
                message=f"{label}: {match.pages} pages, max {filters.max_pages}",
            )
        )
    elif pages is Check.UNKNOWN:
        unknowns.append(
            Unknown(rule=Rule.MAX_PAGES, index=index, message=f"{label}: page count unknown")
        )

    cited = [b.title for b in profile.read_books if mentions_title(rec.reason, b.title)]
    cites_library = Check.PASS if cited else Check.FAIL
    if not cited:
        violations.append(
            Violation(
                rule=Rule.CITES_LIBRARY,
                index=index,
                message=f"{label}: the reason cites no book from the reader's history",
            )
        )

    item = CheckedRecommendation(
        recommendation=rec,
        match=match,
        library_match=library_match,
        cited_titles=cited,
        exists=exists,
        not_in_library=not_in_library,
        language=language,
        max_pages=pages,
        cites_library=cites_library,
    )
    return item, violations, unknowns


def _library_collision(
    rec: Recommendation, match: CatalogBook | None, profile: Profile
) -> Book | None:
    """The profile book this recommendation is, by ISBN or by title and author.

    Both the model's title and the resolved catalog title are compared, so a different
    subtitle or series marker on either side still matches.
    """
    for book in profile.books:
        if same_work(rec.title, [rec.author], book.title, [book.author]):
            return book
        if match is not None:
            if same_work(match.title, match.authors, book.title, [book.author]):
                return book
            shared = {match.isbn, match.isbn13} & {book.isbn, book.isbn13} - {None}
            if shared:
                return book
    return None


def _language_check(book: CatalogBook, filters: Filters) -> Check:
    if filters.language is None:
        return Check.SKIPPED
    if book.language is None:
        return Check.UNKNOWN
    return Check.PASS if book.language == filters.language else Check.FAIL


def _pages_check(book: CatalogBook, filters: Filters) -> Check:
    if filters.max_pages is None:
        return Check.SKIPPED
    if book.pages is None:
        return Check.UNKNOWN
    return Check.PASS if book.pages <= filters.max_pages else Check.FAIL


def _duplicates(
    recommendations: Sequence[Recommendation], resolutions: Sequence[_Resolution]
) -> list[Violation]:
    violations: list[Violation] = []
    for j, later in enumerate(recommendations):
        for i in range(j):
            earlier = recommendations[i]
            same_text = same_work(earlier.title, [earlier.author], later.title, [later.author])
            mi, mj = resolutions[i].match, resolutions[j].match
            same_edition = mi is not None and mj is not None and mi.source_id == mj.source_id
            if same_text or same_edition:
                violations.append(
                    Violation(
                        rule=Rule.DUPLICATE,
                        index=j,
                        message=f"{later.title!r} repeats recommendation {i + 1}",
                    )
                )
                break
    return violations
