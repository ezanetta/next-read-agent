import pytest
from pydantic import HttpUrl

from book_agent.domain.models import CatalogBook
from book_agent.ports import BookSearch
from tests.fakes import FakeBookSearch


def catalog_book(source_id: str, title: str, author: str, language: str) -> CatalogBook:
    return CatalogBook(
        source_id=source_id,
        title=title,
        authors=(author,),
        language=language,
        link=HttpUrl(f"https://books.google.com/books?id={source_id}"),
    )


DUNE = catalog_book("dune", "Dune", "Frank Herbert", "en")
DUNE_ES = catalog_book("dune-es", "Dune (edición en español)", "Frank Herbert", "es")
FICCIONES = catalog_book("ficciones", "Ficciones", "Jorge Luis Borges", "es")


@pytest.fixture
def fake() -> FakeBookSearch:
    return FakeBookSearch([DUNE, DUNE_ES, FICCIONES])


def test_fake_satisfies_the_protocol(fake: FakeBookSearch) -> None:
    # Checked by pyright, not at runtime: this line fails type checking if
    # FakeBookSearch's methods drift from the BookSearch Protocol.
    search: BookSearch = fake
    assert search is fake


async def test_search_matches_title_case_insensitively(fake: FakeBookSearch) -> None:
    assert await fake.search("DUNE") == [DUNE, DUNE_ES]


async def test_search_matches_authors(fake: FakeBookSearch) -> None:
    assert await fake.search("borges") == [FICCIONES]


async def test_search_filters_by_language(fake: FakeBookSearch) -> None:
    assert await fake.search("dune", language="es") == [DUNE_ES]


async def test_search_respects_max_results(fake: FakeBookSearch) -> None:
    assert await fake.search("dune", max_results=1) == [DUNE]


async def test_search_records_calls(fake: FakeBookSearch) -> None:
    await fake.search("dune", language="es", max_results=5)
    assert fake.searches == [("dune", "es", 5)]


async def test_get_details_returns_book_or_none(fake: FakeBookSearch) -> None:
    assert await fake.get_details("ficciones") == FICCIONES
    assert await fake.get_details("missing") is None
    assert fake.detail_lookups == ["ficciones", "missing"]
