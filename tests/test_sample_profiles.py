import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from book_agent.domain.models import ReadingStatus
from book_agent.sample_profiles import (
    CATEGORIES,
    READ_BOOKS,
    Candidate,
    Category,
    Edition,
    Selection,
    candidates,
    load_exclusions,
    pick_edition,
    preset,
    review,
    verify,
)

CLASSICS = Category("c", "A reader", "Classics", "q", "eng", ("juvenile", "drama"))
LATIN = Category("l", "Un lector", "Latin", "q", "spa", original_language_only=True)


def doc(title: str, author: str = "A", **extra: Any) -> dict[str, Any]:
    return {"key": f"/works/{title}", "title": title, "author_name": [author], "cover_i": 1,
            "readinglog_count": 10, "subject": ["Fiction"], **extra}  # fmt: skip


def test_candidates_follow_the_subject_cover_author_rules() -> None:
    docs: list[dict[str, Any]] = [
        doc("Kept"),
        doc("Kids", subject=["Juvenile fiction"]),
        doc("Play", subject=["Drama", "Fiction"]),
        doc("Coverless", cover_i=None),
        {**doc("Anonymous"), "author_name": []},
        *(doc(f"Prolific {i}", author="Busy") for i in range(4)),
    ]
    kept, dropped = candidates(docs, CLASSICS)
    assert [c.title for c in kept] == ["Kept", "Prolific 0", "Prolific 1", "Prolific 2"]
    assert dropped == {
        "excluded subject": 2, "no cover": 1, "no author with a Latin-script name": 1,
        "more than 3 by the author": 1,
    }  # fmt: skip


def edition(
    title: str, language: str, isbn: str | None = "978-0-14-118776-1", year: int = 2000
) -> dict[str, Any]:
    return {"title": title, "languages": [{"key": f"/languages/{language}"}],
            "publish_date": f"March {year}", **({"isbn_13": [isbn]} if isbn else {})}  # fmt: skip


def test_the_edition_shown_is_in_the_categorys_language_with_an_isbn() -> None:
    editions = [
        edition("Ana Karénina", "spa"),
        edition("Anna K", "eng", None),
        edition("Anna Karenina", "eng"),
    ]
    assert pick_edition("Анна Каренина", editions, "eng", False) == (
        Edition(title="Anna Karenina", isbn13="9780141187761"),
        None,
    )
    assert pick_edition("X", [edition("X", "fre")], "eng", False)[1] == (
        "no edition in the language with the work's title and an ISBN-13"
    )


def test_a_latin_title_is_shown_as_is_with_the_isbn_of_a_matching_edition() -> None:
    editions = [
        edition("Zhe Sdang Gi Rgun 'brum", "eng", "978-7-00-000000-1"),
        edition("The Grapes of Wrath (Penguin Classics)", "eng"),
    ]
    assert pick_edition("The Grapes of Wrath", editions, "eng", False) == (
        Edition(title="The Grapes of Wrath", isbn13="9780141187761"),
        None,
    )


def test_work_titles_lose_series_notes_and_bilingual_parts() -> None:
    editions = [edition("Satanás", "spa")]
    found, _ = pick_edition("Satanas / Satan (Biblioteca Breve)", editions, "spa", False)
    assert found is not None and found.title == "Satanas"


def test_edition_titles_lose_series_notes_and_authors_need_a_latin_name() -> None:
    editions = [edition("The Master and Margarita (Vintage Classics)", "eng")]
    found, _ = pick_edition("Мастер и Маргарита", editions, "eng", False)
    assert found is not None and found.title == "The Master and Margarita"
    kept, dropped = candidates(
        [
            doc("A", author="刘慈欣", author_alternative_name=["Vincent Schmitt"]),
            doc("B", author="Mikhail Bulgakov"),
        ],
        CLASSICS,
    )
    assert [c.author for c in kept] == ["Mikhail Bulgakov"]  # never a translator's name
    assert dropped == {"no author with a Latin-script name": 1}


def test_originally_spanish_means_the_earliest_edition_is_spanish() -> None:
    original = [
        edition("One Hundred Years", "eng", year=1970),
        edition("Cien años de soledad", "spa", year=1967),
    ]
    assert pick_edition("Cien años de soledad", original, "spa", True)[0] is not None
    # A Spanish edition that kept the English title doesn't make it Spanish.
    translated = [
        edition("Brave New World", "spa", year=1990),
        edition("Brave New World", "eng", year=1932),
    ]
    assert pick_edition("Brave New World", translated, "spa", True) == (
        None,
        "not originally in the category's language",
    )


def candidate(title: str = "T") -> Candidate:
    return Candidate(key=f"/works/{title}", title=title, author="A", year=1900, readinglog=5,
                     cover_id=7)  # fmt: skip


def test_the_owners_exclusions_drop_books_so_the_next_ones_move_up() -> None:
    kept, dropped = candidates([doc("A"), doc("B"), doc("C")], CLASSICS, {"/works/B": "romance"})
    assert [c.title for c in kept] == ["A", "C"] and dropped == {"excluded by the owner": 1}


def test_fiction_lists_drop_memoirs_and_nonfiction_drops_textbooks_and_self_help() -> None:
    fiction = [c for c in CATEGORIES if c.id != "sample-nonfiction"]
    memoir = doc("Life", subject=["Fiction", "Memoir"])
    assert all(candidates([memoir], c)[0] == [] for c in fiction)
    nonfiction = next(c for c in CATEGORIES if c.id == "sample-nonfiction")
    for subject in ("Textbooks", "Self-help techniques", "Study guides"):
        assert candidates([doc("X", subject=[subject])], nonfiction)[0] == []


def test_exclusions_file_is_validated(tmp_path: Path) -> None:
    path = tmp_path / "exclusions.json"
    assert load_exclusions(path) == {}
    path.write_text(json.dumps({"sample-mystery": {"/works/OL1W": "romance"}}))
    assert load_exclusions(path) == {"sample-mystery": {"/works/OL1W": "romance"}}
    path.write_text(json.dumps({"sample-mystery": {"/works/OL1W": " "}}))
    with pytest.raises(ValueError, match="needs a work key"):
        load_exclusions(path)
    path.write_text(json.dumps({"sample-poetry": {}}))
    with pytest.raises(ValueError, match="unknown category"):
        load_exclusions(path)


def test_the_committed_exclusions_are_valid() -> None:
    assert set(load_exclusions()) <= {c.id for c in CATEGORIES} | {"rejected-isbns"}


def test_the_preset_reads_the_top_50_and_shelves_the_next_5() -> None:
    chosen = [(candidate(f"W{i}"), Edition(title=f"Book {i}", isbn13="9780141187761"))
              for i in range(60)]  # fmt: skip
    built = preset(Selection(CLASSICS, chosen=chosen), date(2026, 11, 25))
    statuses = [b.status for b in built.profile.books]
    assert len(statuses) == 55 and statuses.count(ReadingStatus.READ) == READ_BOOKS
    assert built.profile.ratings_implied and len(built.profile.liked_books) == READ_BOOKS
    assert built.profile.books[50].my_rating is None
    assert built.covers[0] == "https://covers.openlibrary.org/b/id/7-M.jpg"
    assert built.provenance is not None and "2026-11-25" in built.provenance
    assert "| 51 | Book 50 | A | to-read |" in review([Selection(CLASSICS, chosen=chosen)])
    assert built.profile.books[0].year is None


def test_the_five_categories_are_the_agreed_ones() -> None:
    assert [c.id for c in CATEGORIES] == [
        "sample-classics", "sample-sf-fantasy", "sample-mystery", "sample-latin-american",
        "sample-nonfiction",
    ]  # fmt: skip
    assert [c.language for c in CATEGORIES].count("spa") == 1


async def test_verify_reports_books_google_books_does_not_confirm() -> None:
    from pydantic import HttpUrl

    from book_agent.demo_profiles import PresetFile
    from book_agent.domain.models import Book, CatalogBook, Profile
    from tests.fakes import FakeBookSearch

    class ByIsbn(FakeBookSearch):
        async def search(self, query: str, *, language: str | None = None, max_results: int = 10):
            isbn = query.removeprefix("isbn:")
            return [b for b in self.books if b.isbn13 == isbn][:max_results]

    dune = CatalogBook(source_id="d", title="Dune", authors=("Frank Herbert",),
                       isbn13="9780441013593", link=HttpUrl("https://example.com"))  # fmt: skip
    books = [
        Book(title="Dune", author="Frank Herbert", isbn13="9780441013593",
             my_rating=5.0, status=ReadingStatus.READ),
        Book(title="Ghost", author="Nobody", isbn13="9780000000002",
             my_rating=5.0, status=ReadingStatus.READ),
    ]  # fmt: skip
    presets = {"s": PresetFile(description="d", profile=Profile(name="n", books=books))}
    search = ByIsbn([dune])
    problems = await verify(presets, search, gap_s=0)
    assert problems == ["s: Ghost by Nobody (9780000000002): Google Books has nothing"]
    assert await verify(presets, search, only={"9780441013593"}, gap_s=0) == []


def test_a_rejected_isbn_moves_to_the_next_matching_edition() -> None:
    editions = [
        edition("On the Road", "eng", "978-1-4114-7692-9"),
        edition("On the Road", "eng", "978-0-14-118776-1"),
    ]
    found, _ = pick_edition("On the Road", editions, "eng", False, {"9781411476929"})
    assert found == Edition(title="On the Road", isbn13="9780141187761")


def test_author_notes_are_cleaned() -> None:
    kept, _ = candidates([doc("Phantoms", author="V. S. Ramachandran (neurology)")], CLASSICS)
    assert kept[0].author == "V. S. Ramachandran"


def test_rejected_isbns_are_validated(tmp_path: Path) -> None:
    path = tmp_path / "exclusions.json"
    path.write_text(json.dumps({"rejected-isbns": {"9781411476929": "a guide"}}))
    assert load_exclusions(path)["rejected-isbns"] == {"9781411476929": "a guide"}
    path.write_text(json.dumps({"rejected-isbns": {"123": "short"}}))
    with pytest.raises(ValueError, match="needs a work key"):
        load_exclusions(path)
