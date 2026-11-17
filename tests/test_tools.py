import json

from book_agent.tools import SearchBooksArgs, run_search_books
from tests.fakes import FakeBookSearch
from tests.library_data import catalog_book


async def test_search_returns_compact_books() -> None:
    books = FakeBookSearch([catalog_book("dune", "Dune", "Frank Herbert", 412)])
    result = await run_search_books(SearchBooksArgs(query="dune"), books)

    items = json.loads(result)
    assert items == [{"title": "Dune", "author": "Frank Herbert", "pages": 412, "language": "en"}]


async def test_search_without_results_returns_message() -> None:
    books = FakeBookSearch([])
    result = await run_search_books(SearchBooksArgs(query="dune"), books)

    assert "dune" in result


async def test_search_keeps_accents() -> None:
    books = FakeBookSearch(
        [catalog_book("cien-anos", "Cien años de soledad", "Gabriel García Márquez", 412)]
    )
    result = await run_search_books(SearchBooksArgs(query="Cien años de soledad"), books)

    assert "García" in result
    assert "\\u00ed" not in result
