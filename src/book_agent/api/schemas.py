"""The API's own models: what the UI sends and receives, where it differs from the domain.

Domain models are reused where they already have the right shape (D-043: no mapper layer
between identical models). Every model here appears in the OpenAPI schema, so the UI's
TypeScript types are generated from it.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from book_agent.demo_profiles import MAX_MANUAL_BOOKS, MIN_MANUAL_BOOKS, PickedBook
from book_agent.domain.profiles import ProfileSource

Shelf = Literal["read", "to_read", "currently_reading"]


class ErrorBody(BaseModel):
    """Every error the API returns on purpose."""

    error: Literal[
        "invalid_profile", "not_found", "preset", "search_unavailable", "invalid_query",
        "too_large",
    ]  # fmt: skip
    message: str


class SearchHit(BaseModel):
    """A book from search, with what the UI needs to show it and to pick it."""

    source_id: str
    title: str
    authors: list[str]
    year: int | None
    isbn13: str | None
    cover_url: str | None


class SearchResults(BaseModel):
    query: str
    books: list[SearchHit]


class ManualProfileRequest(BaseModel):
    books: list[PickedBook] = Field(min_length=MIN_MANUAL_BOOKS, max_length=MAX_MANUAL_BOOKS)


class ProfileSummary(BaseModel):
    id: str
    name: str
    source: ProfileSource
    description: str | None
    books: int
    read: int
    liked: int
    to_read: int
    # True for manual lists and samples: show "picked", never stars (D-043, D-044).
    ratings_implied: bool
    expires_at: datetime | None
    top_authors: list[str]  # up to 3, most frequent among read books
    cover_urls: list[str]  # up to 3, of read books with a cover


class LibraryBook(BaseModel):
    """One book in the library view."""

    title: str
    author: str
    year: int | None
    shelf: Shelf
    rating: float | None  # None when unrated, or when the rating is implied (picked)
    picked: bool
    cover_url: str | None


class LibraryPage(BaseModel):
    profile_id: str
    books: list[LibraryBook]
    # Cover lookups still queued for this profile (D-046): the UI refetches until it's 0.
    covers_pending: int
