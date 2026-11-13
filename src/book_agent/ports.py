"""Ports: Protocols for the external services the domain depends on.

Adapters in book_agent.adapters implement these. Code that uses a port only sees
the Protocol and the port-level exceptions, never the adapter's HTTP library.
"""

from typing import Protocol

from book_agent.domain.models import CatalogBook


class BookSearchError(Exception):
    """A catalog search failed (timeout, network error, unexpected HTTP status)."""


class BookSearch(Protocol):
    """Searches a book catalog."""

    async def search(
        self, query: str, *, language: str | None = None, max_results: int = 10
    ) -> list[CatalogBook]:
        """Return up to `max_results` books matching `query`, most relevant first.

        `language` is an ISO 639-1 code ("en", "es") that restricts the results.
        Raises BookSearchError if the search fails.
        """
        ...

    async def get_details(self, volume_id: str) -> CatalogBook | None:
        """Return the book with this id, or None if it does not exist.

        Raises BookSearchError if the lookup fails for any other reason.
        """
        ...
