"""Ports: Protocols for the external services the domain depends on.

Adapters in book_agent.adapters implement these. Code that uses a port only sees
the Protocol and the port-level exceptions, never the adapter's HTTP library.
"""

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Protocol

from book_agent.domain.covers import CachedCover
from book_agent.domain.library import RelatedBook
from book_agent.domain.models import CatalogBook
from book_agent.domain.profiles import StoredProfile
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


class ProfileStore(Protocol):
    """Keeps the demo's temporary profiles (D-043). Async: Postgres is a network call (D-048)."""

    async def save(self, stored: StoredProfile) -> None: ...

    async def get(self, profile_id: str, now: datetime) -> StoredProfile | None:
        """The profile, or None when it doesn't exist or has expired (then it's deleted)."""
        ...

    async def delete(self, profile_id: str) -> bool:
        """Delete now; False when there was nothing to delete."""
        ...

    async def purge_expired(self, now: datetime) -> int:
        """Delete every expired profile; returns how many."""
        ...


class SearchCache(Protocol):
    """Book search results by normalized query, so repeated searches cost no quota."""

    async def get(self, query: str, now: datetime, max_age: timedelta) -> list[CatalogBook] | None:
        """Cached results younger than `max_age`, or None."""
        ...

    async def put(self, query: str, books: Sequence[CatalogBook], now: datetime) -> None: ...


class CoverLookupError(Exception):
    """A cover search that couldn't be done (network, rate limit, unexpected response)."""


class CoverFinder(Protocol):
    """Finds a book's cover id (D-046). One request per call: the caller paces them.
    Both raise CoverLookupError when the request fails."""

    async def edition_cover_id(self, isbn13: str) -> int | None:
        """The cover id of the edition with this ISBN, or None when the edition is unknown
        or has no cover."""
        ...

    async def search_cover_id(self, title: str, author: str) -> int | None:
        """A cover id for this title and author from search: an edition whose title
        matches if there is one, else the work's cover, or None."""
        ...


class CoverCache(Protocol):
    """Cover lookups by cover_key, shared by every profile and the owner export."""

    async def get_cover(self, key: str) -> CachedCover | None: ...

    async def put_cover(self, key: str, cover: CachedCover) -> None: ...
