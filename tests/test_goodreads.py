import io
import logging
from pathlib import Path

import pytest

from book_agent.adapters.goodreads import load_goodreads_export, parse_goodreads_csv
from book_agent.domain.models import Book, Profile, ReadingStatus

FIXTURE = Path(__file__).parent / "fixtures" / "goodreads_sample.csv"
REAL_EXPORT = Path(__file__).parents[1] / "data" / "goodreads_library_export.csv"


@pytest.fixture
def profile() -> Profile:
    return load_goodreads_export(FIXTURE, profile_name="Sample")


def by_id(profile: Profile, goodreads_id: str) -> Book:
    return next(b for b in profile.books if b.goodreads_id == goodreads_id)


def test_profile_gets_the_given_name(profile: Profile) -> None:
    assert profile.name == "Sample"


def test_parses_a_complete_row(profile: Profile) -> None:
    assert by_id(profile, "1001") == Book(
        goodreads_id="1001",
        title="The Glass Lighthouse",
        author="Ada Marlow",
        isbn="0123456789",
        isbn13="9780123456786",
        my_rating=5,
        pages=320,
        year=2011,
        status=ReadingStatus.READ,
    )


def test_unwraps_isbns_and_maps_empty_ones_to_none(profile: Profile) -> None:
    rivers = by_id(profile, "1002")
    assert rivers.isbn is None
    assert rivers.isbn13 is None

    clouds = by_id(profile, "1003")
    assert clouds.isbn is None
    assert clouds.isbn13 == "9781111111113"


def test_parses_whole_and_fractional_ratings(profile: Profile) -> None:
    assert by_id(profile, "1001").my_rating == 5.0  # "5"
    assert by_id(profile, "1002").my_rating == 4.5
    assert by_id(profile, "1003").my_rating == 2.75
    assert by_id(profile, "1007").my_rating == 5.0  # "5.0"


@pytest.mark.parametrize("goodreads_id", ["1004", "1005"])  # "0.0" and "0"
def test_rating_zero_means_unrated(profile: Profile, goodreads_id: str) -> None:
    assert by_id(profile, goodreads_id).my_rating is None


def test_maps_exclusive_shelf_to_status(profile: Profile) -> None:
    assert by_id(profile, "1001").status is ReadingStatus.READ
    assert by_id(profile, "1005").status is ReadingStatus.TO_READ
    assert by_id(profile, "1006").status is ReadingStatus.CURRENTLY_READING


def test_empty_page_count_is_none(profile: Profile) -> None:
    assert by_id(profile, "1002").pages is None


def test_year_prefers_original_publication_year(profile: Profile) -> None:
    assert by_id(profile, "1001").year == 2011  # edition is 2015
    assert by_id(profile, "1005").year == -380  # ancient work, edition is 2004


def test_year_falls_back_to_year_published(profile: Profile) -> None:
    assert by_id(profile, "1002").year == 2019


def test_year_is_none_when_both_are_empty(profile: Profile) -> None:
    assert by_id(profile, "1003").year is None


def test_quoted_title_with_comma(profile: Profile) -> None:
    assert by_id(profile, "1003").title == "A Short History of Clouds, Volume 1"


def test_invalid_rows_are_skipped_with_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        profile = load_goodreads_export(FIXTURE, profile_name="Sample")

    ids = {b.goodreads_id for b in profile.books}
    assert ids == {"1001", "1002", "1003", "1004", "1005", "1006", "1007"}

    messages = [r.getMessage() for r in caplog.records]
    assert len(messages) == 5
    assert "did-not-finish" in messages[0]  # unknown exclusive shelf
    assert "<no title>" in messages[1]  # empty title
    assert "lots" in messages[2]  # page count that is not a number
    assert "Out Of Range Rating" in messages[3]  # rating above 5
    assert messages[4] == "Skipped 4 of 11 rows in Goodreads export"


def test_no_summary_warning_when_nothing_is_skipped(caplog: pytest.LogCaptureFixture) -> None:
    header, first_row = FIXTURE.read_text(encoding="utf-8").splitlines()[:2]
    with caplog.at_level(logging.WARNING):
        parse_goodreads_csv(io.StringIO(f"{header}\n{first_row}\n"), profile_name="Sample")
    assert caplog.records == []


def test_profile_helpers_work_on_parsed_data(profile: Profile) -> None:
    assert {b.goodreads_id for b in profile.liked_books} == {"1001", "1002"}
    assert {b.goodreads_id for b in profile.not_so_liked_books} == {"1003"}
    assert {b.goodreads_id for b in profile.to_read_books} == {"1005", "1007"}


def test_missing_required_columns_fails_fast() -> None:
    csv_text = "Title,Author\nSome Book,Someone\n"
    with pytest.raises(ValueError, match="Not a Goodreads export"):
        parse_goodreads_csv(io.StringIO(csv_text), profile_name="Sample")


def test_header_only_file_gives_empty_profile() -> None:
    header = FIXTURE.read_text(encoding="utf-8").splitlines()[0]
    profile = parse_goodreads_csv(io.StringIO(header + "\n"), profile_name="Sample")
    assert profile.books == []


@pytest.mark.skipif(not REAL_EXPORT.exists(), reason="no local Goodreads export in data/")
def test_real_export_loads(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        profile = load_goodreads_export(REAL_EXPORT, profile_name="Me")
    skipped = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Skipping")]
    print(
        f"\nReal export: {len(profile.books)} books, "
        f"{len(profile.read_books)} read, "
        f"{len(profile.to_read_books)} to-read, "
        f"{len(profile.liked_books)} liked, "
        f"{len(skipped)} skipped"
    )
    for message in skipped[:5]:
        print(f"  {message}")
    assert profile.books
