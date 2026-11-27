"""Book search for building a manual list: Google Books behind a cache (D-043).

The query is normalized (case, spacing) so "Dune " and "dune" share one cache entry.
Only cache misses reach Google Books and use the shared daily quota.
"""

from datetime import datetime, timedelta

from book_agent.domain.models import CatalogBook
from book_agent.ports import BookSearch, SearchCache

SEARCH_RESULTS = 8
CACHE_MAX_AGE = timedelta(days=7)
MIN_QUERY, MAX_QUERY = 2, 100


def normalize_query(query: str) -> str:
    return " ".join(query.casefold().split())


async def search_books_cached(
    query: str, search: BookSearch, cache: SearchCache, now: datetime
) -> tuple[list[CatalogBook], bool]:
    """Up to 8 books for `query`, and whether they came from the cache.

    Raises ValueError for a query outside 2-100 characters after normalizing, and the
    port's BookSearchError when Google Books fails (nothing is cached then).
    """
    key = normalize_query(query)
    if not MIN_QUERY <= len(key) <= MAX_QUERY:
        raise ValueError(f"the query must be {MIN_QUERY} to {MAX_QUERY} characters")
    cached = await cache.get(key, now, CACHE_MAX_AGE)
    if cached is not None:
        return cached, True
    books = await search.search(key, max_results=SEARCH_RESULTS)
    await cache.put(key, books, now)
    return books, False
