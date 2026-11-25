from pydantic import HttpUrl

from book_agent.domain.models import CatalogBook
from book_agent.ports import BookSearchError
from tests.api_support import client, services
from tests.fakes import FakeBookSearch

DUNE = CatalogBook(source_id="dune", title="Dune", authors=("Frank Herbert",), year=1965,
                   isbn13="9780441013593", cover_url=HttpUrl("https://img/dune.jpg"),
                   link=HttpUrl("https://example.com/dune"))  # fmt: skip


async def test_search_returns_hits_and_caches_by_normalized_query() -> None:
    search = FakeBookSearch([DUNE])
    async with client(services(books=search)) as c:
        first = await c.get("/books/search", params={"q": "  Dune "})
        second = await c.get("/books/search", params={"q": "dune"})
    assert first.status_code == 200
    assert first.json() == {"query": "dune", "books": [{
        "source_id": "dune", "title": "Dune", "authors": ["Frank Herbert"], "year": 1965,
        "isbn13": "9780441013593", "cover_url": "https://img/dune.jpg",
    }]}  # fmt: skip
    assert second.json() == first.json()
    assert len(search.searches) == 1  # the second came from the cache


async def test_short_and_blank_queries_are_refused() -> None:
    async with client(services()) as c:
        assert (await c.get("/books/search", params={"q": "d"})).status_code == 422
        blank = await c.get("/books/search", params={"q": "   "})
    assert blank.status_code == 400 and blank.json()["error"] == "invalid_query"


class _Down(FakeBookSearch):
    async def search(self, query: str, *, language: str | None = None, max_results: int = 10):
        raise BookSearchError("HTTP 503")


async def test_a_search_outage_is_a_503() -> None:
    async with client(services(books=_Down())) as c:
        response = await c.get("/books/search", params={"q": "dune"})
    assert response.status_code == 503 and response.json()["error"] == "search_unavailable"
