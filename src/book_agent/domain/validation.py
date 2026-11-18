"""Hard-rule validation of recommendations, shared by the baseline, the agent and the evals.

Imports the BookSearch, WorkResolver and SameWorkJudge ports, never an HTTP library or an
LLM SDK.
"""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass

from book_agent.domain.isbn import preferred_isbn13, to_isbn13
from book_agent.domain.matching import authors_match, mentions_title, same_work
from book_agent.domain.models import Book, CatalogBook, Profile, ReadingStatus
from book_agent.domain.recommendations import (
    EXPECTED_RECOMMENDATIONS,
    Check,
    CheckedRecommendation,
    Filters,
    Lookup,
    MatchEvidence,
    Recommendation,
    Rule,
    Unknown,
    ValidationResult,
    Violation,
)
from book_agent.domain.works import SameWorkAnswer, WorkRef
from book_agent.ports import (
    BookSearch,
    BookSearchError,
    SameWorkJudge,
    WorkLookupError,
    WorkResolver,
)

_SEARCH_RESULTS = 5

_LIBRARY_RULES = {
    ReadingStatus.READ: (Rule.ALREADY_READ, "already read"),
    ReadingStatus.TO_READ: (Rule.ON_TO_READ, "already on the to-read list"),
    ReadingStatus.CURRENTLY_READING: (Rule.CURRENTLY_READING, "currently being read"),
}


@dataclass(frozen=True)
class WorkMatching:
    """How to detect translations and retitled editions of books in the library.

    Both run only for recommendations whose author is already in the library: first the
    work keys (deterministic, when a resolver is given), then the judge if they don't match.
    """

    judge: SameWorkJudge
    resolver: WorkResolver | None = None


@dataclass(frozen=True)
class _Resolution:
    match: CatalogBook | None  # the best edition for the filters
    lookup_error: str | None = None
    editions: tuple[CatalogBook, ...] = ()  # every edition matching title and author
    found_by: Lookup | None = None


@dataclass(frozen=True)
class _LibraryCheck:
    book: Book | None = None  # the library book it collides with
    evidence: MatchEvidence | None = None
    error: str | None = None  # the same-work check failed: undecided, not a pass


async def validate_recommendations(
    recommendations: Sequence[Recommendation],
    profile: Profile,
    filters: Filters,
    books: BookSearch,
    work_matching: WorkMatching | None = None,
) -> ValidationResult:
    """Check every hard rule and report all violations and unknowns, not just pass/fail.

    Without `work_matching`, the library check uses ISBN and title variants only.
    """
    resolutions = await asyncio.gather(*(_resolve(r, filters, books) for r in recommendations))
    library_checks = await asyncio.gather(
        *(
            _check_library(rec, resolution, profile, work_matching)
            for rec, resolution in zip(recommendations, resolutions, strict=True)
        )
    )

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
    checks = zip(recommendations, resolutions, library_checks, strict=True)
    for index, (rec, resolution, library) in enumerate(checks):
        item, item_violations, item_unknowns = _check_one(
            index, rec, resolution, library, profile, filters
        )
        items.append(item)
        violations += item_violations
        unknowns += item_unknowns

    return ValidationResult(items=items, violations=violations, unknowns=unknowns)


# --- Resolution ---------------------------------------------------------------


async def _resolve(rec: Recommendation, filters: Filters, books: BookSearch) -> _Resolution:
    """Find the catalog edition that best meets the filters.

    Searches with the language filter first; if no edition matches title and author,
    searches again without it, so a book that exists only in another language is
    reported as a language failure rather than as not found. If the free-text search
    still finds no match, a stricter title + author lookup gets one try before the book
    is reported as not found: search ranking alone must not reject a real book (D-019).
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
        found_by = Lookup.SEARCH
        if not matches:
            matches = _matching(
                rec, await books.find(rec.title, rec.author, max_results=_SEARCH_RESULTS)
            )
            found_by = Lookup.FALLBACK
    except BookSearchError as e:
        return _Resolution(match=None, lookup_error=str(e))
    if not matches:
        return _Resolution(match=None)
    # Best edition: fewest failed filters, then fewest unknowns; ties keep search order.
    best = min(matches, key=lambda c: _filter_score(c, filters))
    return _Resolution(match=best, editions=tuple(matches), found_by=found_by)


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
    library: _LibraryCheck,
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

    library_match = library.book
    if library_match is not None:
        not_in_library = Check.FAIL
        rule, why = _LIBRARY_RULES[library_match.status]
        violations.append(
            Violation(
                rule=rule,
                index=index,
                message=f"{label}: {why} (as {library_match.title!r}"
                f"{_decided_by(library.evidence)})",
                evidence=library.evidence,
            )
        )
    elif library.error is not None:
        not_in_library = Check.UNKNOWN
        unknowns.append(
            Unknown(
                rule=Rule.SAME_WORK,
                index=index,
                message=f"{label}: the author is in the library, but the same-work check "
                f"failed ({library.error})",
            )
        )
    else:
        not_in_library = Check.PASS

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

    mentioned = [b for b in profile.read_books if mentions_title(rec.reason, b.title)]
    # Citing the recommended book itself is no evidence of taste (F-004): "since you loved
    # The Pearl, read The Pearl". It is the same work by title, or the library book it
    # collides with (a translation, found by work key or the judge).
    cited = [b.title for b in mentioned if not _is_itself(rec, b, library_match)]
    cites_library = Check.PASS if cited else Check.FAIL
    if not cited:
        why = (
            "the reason cites only the recommended book itself"
            if mentioned
            else "the reason cites no book from the reader's history"
        )
        violations.append(
            Violation(rule=Rule.CITES_LIBRARY, index=index, message=f"{label}: {why}")
        )

    item = CheckedRecommendation(
        recommendation=rec,
        match=match,
        found_by=resolution.found_by,
        library_match=library_match,
        library_evidence=library.evidence,
        cited_titles=cited,
        exists=exists,
        not_in_library=not_in_library,
        language=language,
        max_pages=pages,
        cites_library=cites_library,
    )
    return item, violations, unknowns


def _is_itself(rec: Recommendation, book: Book, library_match: Book | None) -> bool:
    """Whether a library book is the recommended book itself, for citation purposes."""
    return book == library_match or same_work(rec.title, [rec.author], book.title, [book.author])


async def _check_library(
    rec: Recommendation,
    resolution: _Resolution,
    profile: Profile,
    work_matching: WorkMatching | None,
) -> _LibraryCheck:
    """Is this recommendation a book already in the library, and what decided it?

    ISBN and title variants first. Then, only for authors already in the library, work
    keys, and the same-work judge when the keys don't match: different keys prove nothing,
    since catalogs split some works. A book that was not found is skipped: it already fails
    NOT_FOUND, so a judge call would be wasted.
    """
    deterministic = _library_collision(rec, resolution.match, profile)
    if deterministic.book is not None or work_matching is None:
        return deterministic
    if resolution.match is None and resolution.lookup_error is None:
        return deterministic

    candidates = [b for b in profile.books if authors_match([rec.author], [b.author])]
    if not candidates:
        return deterministic
    if work_matching.resolver is not None:
        book = await _work_key_collision(resolution.editions, candidates, work_matching.resolver)
        if book is not None:
            return _LibraryCheck(book=book, evidence=MatchEvidence.WORK_KEY)
    verdict = await work_matching.judge.judge(
        WorkRef(title=rec.title, author=rec.author),
        [WorkRef(title=b.title, author=b.author) for b in candidates],
    )
    if verdict.error is not None:
        return _LibraryCheck(error=verdict.error)
    if verdict.candidate is None or not verdict.collides:
        return _LibraryCheck()
    same = verdict.answer is SameWorkAnswer.SAME
    evidence = MatchEvidence.LLM_SAME if same else MatchEvidence.LLM_UNSURE
    return _LibraryCheck(book=candidates[verdict.candidate], evidence=evidence)


async def _work_key_collision(
    editions: Sequence[CatalogBook], candidates: Sequence[Book], resolver: WorkResolver
) -> Book | None:
    """The first candidate whose work key equals the work key of any matching edition.

    A failed or unknown lookup gives no key: the judge still decides those.
    """
    edition_isbns = {isbn for e in editions for isbn in _isbn13s(e.isbn, e.isbn13)}
    candidate_isbns = [
        (b, isbn) for b in candidates if (isbn := preferred_isbn13(b.isbn13, b.isbn))
    ]
    if not edition_isbns or not candidate_isbns:
        return None
    isbns = sorted(edition_isbns | {isbn for _, isbn in candidate_isbns})
    keys = dict(
        zip(isbns, await asyncio.gather(*(_work_key(resolver, i) for i in isbns)), strict=True)
    )
    edition_keys = {keys[isbn] for isbn in edition_isbns} - {None}
    return next((b for b, isbn in candidate_isbns if keys[isbn] in edition_keys), None)


async def _work_key(resolver: WorkResolver, isbn13: str) -> str | None:
    try:
        return await resolver.work_for_isbn(isbn13)
    except WorkLookupError:
        return None


def _library_collision(
    rec: Recommendation, match: CatalogBook | None, profile: Profile
) -> _LibraryCheck:
    """The profile book this recommendation is, by ISBN or by title and author.

    Both the model's title and the resolved catalog title are compared, so a different
    subtitle or series marker on either side still matches. ISBNs are compared in their
    ISBN-13 form, so an ISBN-10 on one side matches the ISBN-13 on the other.
    """
    match_isbns = _isbn13s(match.isbn, match.isbn13) if match else set[str]()
    for book in profile.books:
        if same_work(rec.title, [rec.author], book.title, [book.author]):
            return _LibraryCheck(book=book, evidence=MatchEvidence.TITLE)
        if match is not None:
            if same_work(match.title, match.authors, book.title, [book.author]):
                return _LibraryCheck(book=book, evidence=MatchEvidence.TITLE)
            if match_isbns & _isbn13s(book.isbn, book.isbn13):
                return _LibraryCheck(book=book, evidence=MatchEvidence.ISBN)
    return _LibraryCheck()


def _isbn13s(*values: str | None) -> set[str]:
    return {isbn for value in values if (isbn := to_isbn13(value))}


def _decided_by(evidence: MatchEvidence | None) -> str:
    if evidence is MatchEvidence.WORK_KEY:
        return "; same work key as an edition found"
    if evidence is MatchEvidence.LLM_SAME:
        return "; decided by the LLM same-work check"
    if evidence is MatchEvidence.LLM_UNSURE:
        return "; decided by the LLM same-work check, unsure counted as same"
    return ""


def _language_check(book: CatalogBook, filters: Filters) -> Check:
    if filters.language is None:
        return Check.SKIPPED
    if book.language is None:
        return Check.UNKNOWN
    return Check.PASS if primary_language(book.language) == filters.language else Check.FAIL


def primary_language(code: str) -> str:
    """The primary subtag of a language code: "pt-BR" and "pt_br" -> "pt"."""
    return code.replace("_", "-").split("-", 1)[0].casefold()


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
