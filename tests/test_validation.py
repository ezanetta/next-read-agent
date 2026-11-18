import pytest

from book_agent.domain.models import CatalogBook, Profile
from book_agent.domain.recommendations import (
    Check,
    Filters,
    Lookup,
    MatchEvidence,
    Recommendation,
    Rule,
    ValidationResult,
)
from book_agent.domain.validation import WorkMatching, primary_language, validate_recommendations
from book_agent.domain.works import SameWorkAnswer, SameWorkVerdict, WorkRef
from book_agent.ports import BookSearchError
from tests.fakes import FakeBookSearch, FakeSameWorkJudge, FakeWorkResolver
from tests.library_data import (
    BLINDNESS_PT_BR,
    CATALOG,
    CLEAN_THREE,
    FOUNDATION,
    HYPERION_SHORT,
    METAMORPHOSIS,
    NO_LANGUAGE,
    PEDRO_ES,
    PROFILE,
    TO_READ,
    WELL_OF_ASCENSION,
    book,
    catalog,
    catalog_book,
    rec,
)

NO_FILTERS = Filters()
SAME, UNSURE = SameWorkAnswer.SAME, SameWorkAnswer.UNSURE


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


# --- Work-level matching: translations and retitled editions ------------------------

LA_METAMORFOSIS = book("La metamorfosis", "Franz Kafka")
KAFKA_READER = Profile(
    name="Reader",
    books=[
        *PROFILE.books,
        book("El proceso", "Franz Kafka", TO_READ),
        LA_METAMORFOSIS,
    ],
)
METAMORPHOSIS_REC = rec("The Metamorphosis", "Franz Kafka", "A short classic, like Ficciones.")


async def validate_with_judge(
    recommendations: list[Recommendation],
    judge: FakeSameWorkJudge,
    profile: Profile = KAFKA_READER,
    books: FakeBookSearch | None = None,
    resolver: FakeWorkResolver | None = None,
) -> ValidationResult:
    return await validate_recommendations(
        recommendations,
        profile,
        NO_FILTERS,
        books or catalog(),
        WorkMatching(judge=judge, resolver=resolver),
    )


async def test_translated_title_of_a_read_book_is_already_read() -> None:
    # F-001, found in a real run: "La metamorfosis" read in Spanish, "The Metamorphosis"
    # recommended in English, and the validator reported PASSED. Title matching can't see
    # it; the same-work judge decides, and the violation says so.
    judge = FakeSameWorkJudge({"The Metamorphosis": SameWorkVerdict(answer=SAME, candidate=1)})
    result = await validate_with_judge(with_clean(METAMORPHOSIS_REC), judge)

    assert result.items[0].match == METAMORPHOSIS
    assert rules(result) == [(Rule.ALREADY_READ, 0)]
    violation = result.violations[0]
    assert violation.evidence is MatchEvidence.LLM_SAME and violation.evidence.by_llm
    assert violation.message == (
        "'The Metamorphosis' by Franz Kafka: already read "
        "(as 'La metamorfosis'; decided by the LLM same-work check)"
    )
    assert result.items[0].library_match == LA_METAMORFOSIS
    assert result.items[0].library_evidence is MatchEvidence.LLM_SAME


async def test_judge_sees_only_the_authors_books_in_library_order() -> None:
    judge = FakeSameWorkJudge()
    await validate_with_judge(with_clean(METAMORPHOSIS_REC), judge)

    assert len(judge.calls) == 1  # Simmons, Le Guin and Asimov are not in the library
    book_ref, candidates = judge.calls[0]
    assert book_ref == WorkRef(title="The Metamorphosis", author="Franz Kafka")
    assert candidates == (
        WorkRef(title="El proceso", author="Franz Kafka"),
        WorkRef(title="La metamorfosis", author="Franz Kafka"),
    )


async def test_unsure_counts_as_same_and_says_so() -> None:
    judge = FakeSameWorkJudge({"The Metamorphosis": SameWorkVerdict(answer=UNSURE, candidate=1)})
    result = await validate_with_judge(with_clean(METAMORPHOSIS_REC), judge)

    assert rules(result) == [(Rule.ALREADY_READ, 0)]
    assert result.violations[0].evidence is MatchEvidence.LLM_UNSURE
    assert result.violations[0].message.endswith(
        "decided by the LLM same-work check, unsure counted as same)"
    )


async def test_collision_with_a_to_read_book_uses_its_rule() -> None:
    judge = FakeSameWorkJudge({"The Metamorphosis": SameWorkVerdict(answer=SAME, candidate=0)})
    result = await validate_with_judge(with_clean(METAMORPHOSIS_REC), judge)
    assert rules(result) == [(Rule.ON_TO_READ, 0)]
    assert "(as 'El proceso';" in result.violations[0].message


async def test_different_work_by_a_library_author_passes() -> None:
    result = await validate_with_judge(with_clean(METAMORPHOSIS_REC), FakeSameWorkJudge())
    assert result.violations == [] and result.unknowns == []
    assert result.items[0].not_in_library is Check.PASS
    assert result.items[0].library_evidence is None


async def test_judge_failure_is_a_same_work_unknown_not_a_pass() -> None:
    judge = FakeSameWorkJudge({"The Metamorphosis": SameWorkVerdict(error="LLM call failed: 429")})
    result = await validate_with_judge(with_clean(METAMORPHOSIS_REC), judge)

    assert result.violations == []
    assert [(u.rule, u.index) for u in result.unknowns] == [(Rule.SAME_WORK, 0)]
    assert result.unknowns[0].message == (
        "'The Metamorphosis' by Franz Kafka: the author is in the library, "
        "but the same-work check failed (LLM call failed: 429)"
    )
    assert result.items[0].not_in_library is Check.UNKNOWN
    assert result.passed and not result.fully_verified


async def test_authors_not_in_the_library_never_reach_the_judge() -> None:
    judge = FakeSameWorkJudge()
    result = await validate_with_judge(CLEAN_THREE, judge)
    assert result.fully_verified
    assert judge.calls == []


async def test_deterministic_matches_never_reach_the_judge() -> None:
    judge = FakeSameWorkJudge()
    dune = rec("Dune", "Frank Herbert", "Like Ficciones.")
    result = await validate_with_judge(with_clean(dune), judge)
    assert rules(result) == [(Rule.ALREADY_READ, 0)]
    assert result.violations[0].evidence is MatchEvidence.TITLE
    assert judge.calls == []


async def test_books_not_found_never_reach_the_judge() -> None:
    judge = FakeSameWorkJudge()
    result = await validate_with_judge(with_clean(rec("Amerika Lost", "Franz Kafka")), judge)
    assert rules(result) == [(Rule.NOT_FOUND, 0)]
    assert judge.calls == []


async def test_a_lookup_failure_still_reaches_the_judge() -> None:
    # The book may exist: a failed search is no reason to skip the library check.
    class BrokenSearch(FakeBookSearch):
        async def search(
            self, query: str, *, language: str | None = None, max_results: int = 10
        ) -> list[CatalogBook]:
            raise BookSearchError("Google Books request timed out")

    judge = FakeSameWorkJudge({"The Metamorphosis": SameWorkVerdict(answer=SAME, candidate=1)})
    result = await validate_with_judge([METAMORPHOSIS_REC], judge, books=BrokenSearch())
    assert (Rule.ALREADY_READ, 0) in rules(result)


async def test_without_work_matching_translations_are_missed() -> None:
    # The validator without a judge keeps its old behaviour: ISBN and titles only.
    result = await validate_recommendations(
        with_clean(METAMORPHOSIS_REC), KAFKA_READER, NO_FILTERS, catalog()
    )
    assert result.violations == []


# --- Work keys, before the judge -------------------------------------------------------
# Real ISBNs from the F-003 spike: the Spanish edition in the export, a Bantam English
# edition, and a self-published reprint Open Library doesn't know.

KAFKA_ES, KAFKA_BANTAM, KAFKA_REPRINT = "9789871165070", "9780553213690", "9798569694662"
KAFKA_WORK = "/works/OL498556W"
KAFKA_READER_WITH_ISBN = Profile(
    name="Reader",
    books=[*PROFILE.books, book("La metamorfosis", "Franz Kafka", isbn13=KAFKA_ES)],
)
BANTAM = catalog_book("bantam", "The Metamorphosis", "Franz Kafka", 201, isbn13=KAFKA_BANTAM)
REPRINT = catalog_book("reprint", "The Metamorphosis", "Franz Kafka", 80, isbn13=KAFKA_REPRINT)


async def test_equal_work_keys_decide_without_the_judge() -> None:
    judge = FakeSameWorkJudge()
    resolver = FakeWorkResolver({KAFKA_ES: KAFKA_WORK, KAFKA_BANTAM: KAFKA_WORK})
    result = await validate_with_judge(
        with_clean(METAMORPHOSIS_REC),
        judge,
        profile=KAFKA_READER_WITH_ISBN,
        books=FakeBookSearch([*CATALOG, BANTAM]),
        resolver=resolver,
    )
    assert rules(result) == [(Rule.ALREADY_READ, 0)]
    assert result.violations[0].evidence is MatchEvidence.WORK_KEY
    assert result.violations[0].message.endswith(
        "(as 'La metamorfosis'; same work key as an edition found)"
    )
    assert judge.calls == []


async def test_any_matching_edition_can_carry_the_work_key() -> None:
    # The reprint ranks first and is unknown; the Bantam edition still decides.
    resolver = FakeWorkResolver({KAFKA_ES: KAFKA_WORK, KAFKA_BANTAM: KAFKA_WORK})
    result = await validate_with_judge(
        [METAMORPHOSIS_REC],
        FakeSameWorkJudge(),
        profile=KAFKA_READER_WITH_ISBN,
        books=FakeBookSearch([REPRINT, BANTAM]),
        resolver=resolver,
    )
    assert result.items[0].library_evidence is MatchEvidence.WORK_KEY
    assert sorted(resolver.lookups) == sorted([KAFKA_ES, KAFKA_BANTAM, KAFKA_REPRINT])


async def test_different_work_keys_still_go_to_the_judge() -> None:
    # Open Library splits some works (El principito / The Little Prince): different keys
    # prove nothing, so the judge decides.
    judge = FakeSameWorkJudge({"The Metamorphosis": SameWorkVerdict(answer=SAME, candidate=0)})
    resolver = FakeWorkResolver({KAFKA_ES: "/works/OL1W", KAFKA_BANTAM: "/works/OL2W"})
    result = await validate_with_judge(
        [METAMORPHOSIS_REC],
        judge,
        profile=KAFKA_READER_WITH_ISBN,
        books=FakeBookSearch([BANTAM]),
        resolver=resolver,
    )
    assert result.items[0].library_evidence is MatchEvidence.LLM_SAME
    assert len(judge.calls) == 1


async def test_failed_work_lookups_fall_back_to_the_judge() -> None:
    judge = FakeSameWorkJudge({"The Metamorphosis": SameWorkVerdict(answer=SAME, candidate=0)})
    resolver = FakeWorkResolver({KAFKA_ES: KAFKA_WORK}, failing={KAFKA_BANTAM})
    result = await validate_with_judge(
        [METAMORPHOSIS_REC],
        judge,
        profile=KAFKA_READER_WITH_ISBN,
        books=FakeBookSearch([BANTAM]),
        resolver=resolver,
    )
    assert result.items[0].library_evidence is MatchEvidence.LLM_SAME
    assert result.unknowns == []  # the judge answered, so nothing is left undecided


async def test_no_isbns_means_no_lookups() -> None:
    judge = FakeSameWorkJudge()
    resolver = FakeWorkResolver()
    await validate_with_judge(with_clean(METAMORPHOSIS_REC), judge, resolver=resolver)
    assert resolver.lookups == []  # neither the catalog edition nor the library has an ISBN
    assert len(judge.calls) == 1


async def test_authors_not_in_the_library_cause_no_lookups() -> None:
    resolver = FakeWorkResolver()
    await validate_with_judge(CLEAN_THREE, FakeSameWorkJudge(), resolver=resolver)
    assert resolver.lookups == []


# --- Evidence for deterministic matches ---------------------------------------------


async def test_isbn_match_has_isbn_evidence() -> None:
    result = await validate(with_clean(rec("The Desert Planet Saga", "Frank Herbert")))
    assert result.violations[0].evidence is MatchEvidence.ISBN


async def test_isbn10_in_the_library_matches_isbn13_in_the_catalog() -> None:
    # Same Dune edition: ISBN-10 0441013597 is ISBN-13 9780441013593.
    profile = Profile(
        name="Reader",
        books=[
            PROFILE.books[0].model_copy(update={"isbn": "0441013597", "isbn13": None}),
            *PROFILE.books[1:],
        ],
    )
    result = await validate_recommendations(
        with_clean(rec("The Desert Planet Saga", "Frank Herbert", "Like Ficciones.")),
        profile,
        NO_FILTERS,
        catalog(),
    )
    assert rules(result) == [(Rule.ALREADY_READ, 0)]
    assert result.violations[0].evidence is MatchEvidence.ISBN


# --- Fallback lookup (D-019) ------------------------------------------------------------


class SearchMisses(FakeBookSearch):
    """The free-text search ranks the book out, as Google Books did in F-007."""

    async def search(
        self, query: str, *, language: str | None = None, max_results: int = 10
    ) -> list[CatalogBook]:
        self.searches.append((query, language, max_results))
        return []


async def test_books_found_by_the_search_say_so() -> None:
    books = catalog()
    result = await validate(CLEAN_THREE, books=books)
    assert [item.found_by for item in result.items] == [Lookup.SEARCH] * 3
    assert books.finds == []  # no fallback needed


async def test_fallback_finds_what_the_search_missed() -> None:
    books = SearchMisses(CATALOG)
    result = await validate(CLEAN_THREE, Filters(language="en"), books=books)

    assert result.violations == []
    assert [item.found_by for item in result.items] == [Lookup.FALLBACK] * 3
    assert len(books.searches) == 6  # with and without the language filter, per book
    assert books.finds[0] == ("Hyperion", "Dan Simmons")


async def test_not_found_only_after_the_fallback_also_misses() -> None:
    books = catalog()
    result = await validate(with_clean(rec("The Silent Library", "Nobody Real")), books=books)
    assert rules(result) == [(Rule.NOT_FOUND, 0)]
    assert books.finds == [("The Silent Library", "Nobody Real")]
    assert result.items[0].found_by is None


async def test_a_failing_fallback_is_a_lookup_failure() -> None:
    class FallbackFails(SearchMisses):
        async def find(
            self, title: str, author: str, *, max_results: int = 10
        ) -> list[CatalogBook]:
            raise BookSearchError("Google Books request timed out")

    result = await validate(CLEAN_THREE, books=FallbackFails(CATALOG))
    assert [u.rule for u in result.unknowns] == [Rule.NOT_FOUND] * 3
    assert result.violations == []
