"""Live tests against the real Google Books API. Excluded by default.

Run with: uv run pytest -m live
Uses GOOGLE_BOOKS_API_KEY from .env when set; keyless requests are often rate-limited.
"""

from collections.abc import AsyncIterator

import httpx
import pytest
from pydantic import ValidationError

from book_agent.adapters.google_books import GoogleBooksSearch
from book_agent.config import get_settings

pytestmark = pytest.mark.live


def api_key() -> str | None:
    try:
        key = get_settings().google_books_api_key
    except ValidationError:  # .env incomplete: fall back to a keyless request
        return None
    return key.get_secret_value() if key else None


@pytest.fixture
async def books() -> AsyncIterator[GoogleBooksSearch]:
    async with httpx.AsyncClient(timeout=10) as client:
        yield GoogleBooksSearch(client, api_key=api_key())


async def test_live_search_and_details(books: GoogleBooksSearch) -> None:
    results = await books.search("Dune Frank Herbert", max_results=5)
    assert results, "expected at least one result for a famous book"
    assert all(str(b.link).startswith("https://") for b in results)

    first = results[0]
    details = await books.get_details(first.source_id)
    assert details is not None
    assert details.source_id == first.source_id


async def test_live_language_restriction(books: GoogleBooksSearch) -> None:
    results = await books.search("Borges Ficciones", language="es", max_results=5)
    assert results
    assert all(b.language in (None, "es") for b in results)
