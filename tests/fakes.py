"""In-memory fakes that implement the ports, for tests that should not touch the network."""

from book_agent.domain.models import CatalogBook


class FakeBookSearch:
    """BookSearch backed by a fixed list of books.

    Matches a query case-insensitively against title and authors, and records every
    call so tests can assert on what was asked.
    """

    def __init__(self, books: list[CatalogBook] | None = None) -> None:
        self.books = list(books or [])
        self.searches: list[tuple[str, str | None, int]] = []
        self.detail_lookups: list[str] = []

    async def search(
        self, query: str, *, language: str | None = None, max_results: int = 10
    ) -> list[CatalogBook]:
        self.searches.append((query, language, max_results))
        needle = query.casefold()
        matches = [
            b
            for b in self.books
            if (needle in b.title.casefold() or any(needle in a.casefold() for a in b.authors))
            and (language is None or b.language in (None, language))
        ]
        return matches[:max_results]

    async def get_details(self, volume_id: str) -> CatalogBook | None:
        self.detail_lookups.append(volume_id)
        return next((b for b in self.books if b.source_id == volume_id), None)
