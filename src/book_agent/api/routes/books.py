"""Book search for building a manual list."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from book_agent.api.dependencies import Services
from book_agent.api.schemas import ErrorBody, SearchHit, SearchResults
from book_agent.demo_search import MAX_QUERY, MIN_QUERY, normalize_query, search_books_cached
from book_agent.ports import BookSearchError

router = APIRouter(prefix="/books", tags=["books"])


@router.get(
    "/search",
    operation_id="searchBooks",
    responses={
        400: {"model": ErrorBody, "description": "The query is too short or too long"},
        503: {"model": ErrorBody, "description": "Google Books is unavailable"},
    },
)
async def search_books(
    q: Annotated[str, Query(min_length=MIN_QUERY, max_length=MAX_QUERY)], services: Services
) -> SearchResults:
    """Autocomplete: up to 8 books with covers. Repeated queries come from a cache."""
    try:
        books, _ = await search_books_cached(
            q, services.books, services.search_cache, services.clock()
        )
    except ValueError as e:
        raise HTTPException(400, detail={"error": "invalid_query", "message": str(e)}) from e
    except BookSearchError as e:
        message = "book search is unavailable right now"
        raise HTTPException(503, detail={"error": "search_unavailable", "message": message}) from e
    hits = [
        SearchHit(
            source_id=b.source_id,
            title=b.title,
            authors=list(b.authors),
            year=b.year,
            isbn13=b.isbn13,
            cover_url=str(b.cover_url) if b.cover_url else None,
        )
        for b in books
    ]
    return SearchResults(query=normalize_query(q), books=hits)
