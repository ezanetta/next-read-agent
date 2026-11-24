"""Ports: Protocols for the external services the domain depends on.

Adapters in book_agent.adapters implement these. Code that uses a port only sees
the Protocol and the port-level exceptions, never the adapter's HTTP library.
"""

from collections.abc import Sequence
from typing import Protocol

from book_agent.domain.library import RelatedBook
from book_agent.domain.models import CatalogBook
from book_agent.domain.works import SameWorkVerdict, WorkInfo, WorkRef


class BookSearchError(Exception):
    """A catalog search failed (timeout, network error, unexpected HTTP status)."""


class BookSearch(Protocol):
    """Searches a book catalog."""

    async def search(
        self, query: str, *, language: str | None = None, max_results: int = 10
    ) -> list[CatalogBook]:
        """Return up to `max_results` books matching `query`, most relevant first.

        `language` is an ISO 639-1 code ("en", "es"). When given, no result has a
        different known language, but books whose language is unknown (None) may be
        included: deciding what to do with missing data is up to the caller.
        Fewer than `max_results` books may be returned.
        Raises BookSearchError if the search fails.
        """
        ...

    async def get_details(self, volume_id: str) -> CatalogBook | None:
        """Return the book with this id, or None if it does not exist.

        Raises BookSearchError if the lookup fails for any other reason.
        """
        ...

    async def find(self, title: str, author: str, *, max_results: int = 10) -> list[CatalogBook]:
        """A stricter lookup by title and author, for when a free-text search misses a book.

        Returns candidate editions, most relevant first; the caller decides which match.
        Raises BookSearchError if the lookup fails.
        """
        ...


class SameWorkJudge(Protocol):
    """Decides whether a book is the same work as one of a few candidates.

    Used by the validator for books whose author is already in the reader's library,
    where a translated or retitled edition could slip past title matching.
    """

    async def judge(self, book: WorkRef, candidates: Sequence[WorkRef]) -> SameWorkVerdict:
        """The verdict for `book` against `candidates` (indexes refer to this sequence).

        Never raises: a failed check comes back as a verdict with an error, so the caller
        can report it as unknown instead of crashing or passing silently.
        """
        ...


class WorkLookupError(Exception):
    """A work lookup failed (timeout, network error, unexpected HTTP status or body)."""


class WorkResolver(Protocol):
    """Maps an edition's ISBN to the work it belongs to, to group translations and editions.

    Lookups are by ISBN only: title searches find duplicate and stray works (F-003).
    """

    async def work_for_isbn(self, isbn13: str) -> str | None:
        """The work key for this ISBN-13 (e.g. "/works/OL498556W"), or None if unknown.

        Equal keys mean the same work. Different keys do not prove different works:
        catalogs split some works across several keys.
        Raises WorkLookupError if the lookup fails for any other reason.
        """
        ...


class WorkDescriptions(Protocol):
    """Describes a work by its key: description and subjects (D-041)."""

    async def work_info(self, work_key: str) -> WorkInfo | None:
        """What the catalog says about this work (e.g. "/works/OL498556W"), or None if the
        key is unknown. Raises WorkLookupError if the lookup fails for any other reason.
        """
        ...


class LibraryRetriever(Protocol):
    """Finds the reader's books most related to a request (D-041)."""

    async def related_books(self, query: str) -> list[RelatedBook]:
        """The read books most related to `query`, most related first.

        Raises an LLMError subclass if the query can't be embedded: the caller decides
        whether that stops the run.
        """
        ...
