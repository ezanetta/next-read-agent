import pytest

from book_agent.domain.models import CatalogBook, Profile
from book_agent.domain.recommendations import Check, Filters, Recommendation, Rule, ValidationResult
from book_agent.domain.validation import primary_language, validate_recommendations
from book_agent.ports import BookSearchError
from tests.fakes import FakeBookSearch
from tests.library_data import (
    BLINDNESS_PT_BR,
    CLEAN_THREE,
    FOUNDATION,
    HYPERION_SHORT,
    METAMORPHOSIS,
    NO_LANGUAGE,
    PEDRO_ES,
    PROFILE,
    WELL_OF_ASCENSION,
    book,
    catalog,
    rec,
)

NO_FILTERS = Filters()


async def validate(
    recommendations: list[Recommendation],
    filters: Filters = NO_FILTERS,
    books: FakeBookSearch | None = None,
) -> ValidationResult:
    return await validate_recommendations(recommendations, PROFILE, filters, books or catalog())


def rules(result: ValidationResult) -> list[tuple[Rule, int | None]]:
    return [(v.rule, v.index) for v in result.violations]


def with_clean(first: Recommendation) -> list[Recommendation]:
    """A full list of 3 whose only questionable entry is the first one."""
    return [first, *CLEAN_THREE[1:]]


# --- A clean list ----------------------------------------------------------------


async def test_clean_list_passes_every_rule() -> None:
    result = await validate(CLEAN_THREE)
    assert result.passed
    assert result.fully_verified
    assert result.violations == [] and result.unknowns == []
    for item in result.items:
        assert item.match is not None
        assert (item.exists, item.not_in_library, item.cites_library) == (Check.PASS,) * 3
        assert (item.language, item.max_pages) == (Check.SKIPPED, Check.SKIPPED)
    assert result.items[0].cited_titles == ["Dune (Dune Chronicles, #1)"]


# --- Count and duplicates --------------------------------------------------------


async def test_wrong_count_is_a_list_level_violation() -> None:
    result = await validate(CLEAN_THREE[:2])
    assert rules(result) == [(Rule.WRONG_COUNT, None)]
    assert "expected 3 books, got 2" in result.violations[0].message


async def test_duplicate_is_reported_on_the_repeat() -> None:
    again = rec("HYPERION", "Dan Simmons", "Again like Dune.")  # same book, other spelling
    result = await validate([CLEAN_THREE[0], again, CLEAN_THREE[2]])
    assert rules(result) == [(Rule.DUPLICATE, 1)]


# --- Existence -------------------------------------------------------------------


async def test_book_not_found() -> None:
    result = await validate(with_clean(rec("The Silent Library", "Nobody Real")))
    assert rules(result) == [(Rule.NOT_FOUND, 0)]
    assert result.items[0].exists is Check.FAIL
    assert result.items[0].match is None
    assert result.items[0].language is Check.SKIPPED  # can't check filters without a match


async def test_a_lookup_failure_is_unknown_not_a_violation() -> None:
    class BrokenSearch(FakeBookSearch):
        async def search(
            self, query: str, *, language: str | None = None, max_results: int = 10
        ) -> list[CatalogBook]:
            raise BookSearchError("Google Books request timed out")

    result = await validate(CLEAN_THREE, books=BrokenSearch())
    assert result.violations == []
    assert [u.rule for u in result.unknowns] == [Rule.NOT_FOUND] * 3
    assert "timed out" in result.unknowns[0].message
    assert result.passed and not result.fully_verified
    assert result.items[0].exists is Check.UNKNOWN


# --- Library collisions -----------------------------------------------------------


async def test_already_read_despite_a_different_subtitle() -> None:
    # Library: "Dune (Dune Chronicles, #1)"; model and catalog: "Dune".
    result = await validate(with_clean(rec("Dune", "Frank Herbert", "Like Ficciones.")))
    assert rules(result) == [(Rule.ALREADY_READ, 0)]
    library_match = result.items[0].library_match
    assert library_match is not None and library_match.title == "Dune (Dune Chronicles, #1)"


async def test_mistborn_series_form_matches_the_book_already_read() -> None:
    # Library: "The Final Empire (Mistborn, #1)"; model: "Mistborn: The Final Empire".
    result = await validate(with_clean(rec("Mistborn: The Final Empire", "Brandon Sanderson")))
    assert rules(result) == [(Rule.ALREADY_READ, 0)]


async def test_next_mistborn_book_is_not_reported_as_read() -> None:
    result = await validate(
        with_clean(rec("Mistborn: The Well of Ascension", "Brandon Sanderson", "After Dune."))
    )
    assert result.violations == []
    assert result.items[0].match == WELL_OF_ASCENSION
    assert result.items[0].not_in_library is Check.PASS


async def test_same_isbn_with_a_different_title_is_already_read() -> None:
    result = await validate(with_clean(rec("The Desert Planet Saga", "Frank Herbert")))
    assert rules(result) == [(Rule.ALREADY_READ, 0)]


async def test_book_on_the_to_read_list() -> None:
    result = await validate(with_clean(rec("The Name of the Wind", "Patrick Rothfuss")))
    assert rules(result) == [(Rule.ON_TO_READ, 0)]


async def test_book_currently_being_read() -> None:
    result = await validate(with_clean(rec("Project Hail Mary", "Andy Weir")))
    assert rules(result) == [(Rule.CURRENTLY_READING, 0)]


# --- Page filter -------------------------------------------------------------------


async def test_page_filter_violation_uses_catalog_data() -> None:
    first = rec("The Left Hand of Darkness", "Ursula K. Le Guin", "Like Dune.")
    result = await validate(
        [first, CLEAN_THREE[2], rec("The Name of the Wind", "Patrick Rothfuss")],
        Filters(max_pages=300),
    )
    assert (Rule.MAX_PAGES, 0) in rules(result)  # 304 pages in the catalog
    assert result.items[0].max_pages is Check.FAIL


async def test_best_edition_is_picked_for_the_page_filter() -> None:
    result = await validate(CLEAN_THREE, Filters(max_pages=400))
    assert result.items[0].match == HYPERION_SHORT  # 350 pages, not the 482-page edition
    assert result.fully_verified


async def test_unknown_page_count_is_unknown_not_a_pass() -> None:
    result = await validate(
        with_clean(rec("Solaris", "Stanislaw Lem", "Strange, like Dune.")), Filters(max_pages=400)
    )
    assert result.violations == []
    assert [(u.rule, u.index) for u in result.unknowns] == [(Rule.MAX_PAGES, 0)]
    assert result.items[0].max_pages is Check.UNKNOWN
    assert result.passed and not result.fully_verified


# --- Language filter ----------------------------------------------------------------


async def test_language_filter_finds_the_matching_edition() -> None:
    books = catalog()
    result = await validate(
        with_clean(rec("Pedro Páramo", "Juan Rulfo", "Like Ficciones.")),
        Filters(language="es"),
        books,
    )
    assert result.items[0].match == PEDRO_ES
    assert result.items[0].language is Check.PASS


async def test_language_fallback_reports_a_language_failure_not_not_found() -> None:
    books = catalog()
    result = await validate(
        [rec("Foundation", "Isaac Asimov", "Like Dune.")] * 1
        + [rec("Pedro Páramo", "Juan Rulfo", "Like Ficciones.")] * 2,
        Filters(language="es"),
        books,
    )
    assert result.items[0].match == FOUNDATION  # found by the search without language
    assert (Rule.LANGUAGE, 0) in rules(result)
    assert (Rule.NOT_FOUND, 0) not in rules(result)
    foundation_searches = [s for s in books.searches if s[0].startswith("Foundation")]
    assert [language for _, language, _ in foundation_searches] == ["es", None]


async def test_missing_language_is_unknown() -> None:
    result = await validate(
        with_clean(rec("The Invisible Cities", "Italo Calvino", "Like Ficciones.")),
        Filters(language="en"),
    )
    assert result.items[0].match == NO_LANGUAGE
    assert result.items[0].language is Check.UNKNOWN
    assert [(u.rule, u.index) for u in result.unknowns] == [(Rule.LANGUAGE, 0)]


# --- Citations -----------------------------------------------------------------------


async def test_reason_without_a_citation_is_a_violation() -> None:
    result = await validate(with_clean(rec("Hyperion", "Dan Simmons", "A great classic.")))
    assert rules(result) == [(Rule.CITES_LIBRARY, 0)]
    assert result.items[0].cites_library is Check.FAIL
    assert result.items[0].cited_titles == []


async def test_citing_a_to_read_book_does_not_count() -> None:
    result = await validate(
        with_clean(rec("Hyperion", "Dan Simmons", "Fans of The Name of the Wind will enjoy it."))
    )
    assert rules(result) == [(Rule.CITES_LIBRARY, 0)]


async def test_every_violation_is_reported_not_just_the_first() -> None:
    bad = [rec("Dune", "Frank Herbert", "A classic."), rec("Nope", "Nobody")]
    result = await validate(bad, Filters(max_pages=300))
    assert set(rules(result)) == {
        (Rule.WRONG_COUNT, None),
        (Rule.ALREADY_READ, 0),
        (Rule.MAX_PAGES, 0),
        (Rule.CITES_LIBRARY, 0),
        (Rule.NOT_FOUND, 1),
    }


# --- Regional language codes ----------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "primary"),
    [("pt-BR", "pt"), ("zh-CN", "zh"), ("en", "en"), ("pt_br", "pt"), ("EN-us", "en")],
)
def test_primary_language(code: str, primary: str) -> None:
    assert primary_language(code) == primary


async def test_regional_catalog_language_matches_the_primary_subtag() -> None:
    result = await validate(
        with_clean(rec("Ensaio sobre a Cegueira", "José Saramago", "Bleak, like Ficciones.")),
        Filters(language="pt"),
    )
    assert result.items[0].match == BLINDNESS_PT_BR  # language "pt-BR" in the catalog
    assert result.items[0].language is Check.PASS


# --- Known failure: translated titles ----------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason="needs work-level matching: a translated title of a read book is not detected",
)
async def test_known_failure_translated_title_of_a_read_book() -> None:
    # Found in a real run: "La metamorfosis" read in Spanish, "The Metamorphosis"
    # recommended in English, and the validator reported PASSED. strict=True turns this
    # into a failure once it starts passing, so the marker can't be forgotten.
    profile = Profile(name="Reader", books=[*PROFILE.books, book("La metamorfosis", "Franz Kafka")])
    recommendation = rec("The Metamorphosis", "Franz Kafka", "A short classic, like Ficciones.")
    result = await validate_recommendations(
        with_clean(recommendation), profile, NO_FILTERS, catalog()
    )
    assert result.items[0].match == METAMORPHOSIS
    assert (Rule.ALREADY_READ, 0) in rules(result)
