"""Domain models (Pydantic): Book, Profile and CatalogBook.

Book describes one entry in a reader's library, normalized from any source
(Goodreads CSV export today, manual entry later).
Profile is a named reader plus their books, with helpers that split the library
into the taste signals the agent uses.
CatalogBook is a book that exists in a catalog (a recommendation candidate),
independent of any reader.
"""

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field, HttpUrl

LIKED_MIN_RATING = 4.0

Rating = Annotated[float, Field(gt=0, le=5)]
"""A star rating in (0, 5]. Goodreads allows fractional stars (e.g. 3.5, 2.75).
Unrated books use None, never 0."""


class ReadingStatus(StrEnum):
    READ = "read"
    TO_READ = "to_read"
    CURRENTLY_READING = "currently_reading"


class Book(BaseModel, frozen=True):
    """One book in a reader's library. Immutable, so it is also hashable.

    `year` is the work's original publication year, not the year of the edition
    the reader owns. It can be negative for ancient works (e.g. Plato).
    """

    # None for manually entered books. Matching books across sources is part of
    # the week-2 work vs edition de-duplication (docs/PLAN.md).
    goodreads_id: str | None = None
    title: str = Field(min_length=1)
    author: str
    isbn: str | None = None
    isbn13: str | None = None
    my_rating: Rating | None = None
    pages: int | None = Field(default=None, gt=0)
    year: int | None = None
    status: ReadingStatus


class Profile(BaseModel):
    """A reader and their library."""

    name: str = Field(min_length=1)
    books: list[Book]

    @property
    def read_books(self) -> list[Book]:
        return [b for b in self.books if b.status is ReadingStatus.READ]

    @property
    def to_read_books(self) -> list[Book]:
        return [b for b in self.books if b.status is ReadingStatus.TO_READ]

    @property
    def liked_books(self) -> list[Book]:
        """Read books rated 4.0 or higher."""
        return [
            b
            for b in self.read_books
            if b.my_rating is not None and b.my_rating >= LIKED_MIN_RATING
        ]

    @property
    def not_so_liked_books(self) -> list[Book]:
        """Read books rated below 4.0. Unrated books are neither liked nor not-so-liked."""
        return [
            b for b in self.read_books if b.my_rating is not None and b.my_rating < LIKED_MIN_RATING
        ]


class CatalogBook(BaseModel, frozen=True):
    """A book that exists in a catalog (Google Books today), independent of any reader.

    Unlike Book, it has no reading status or rating: it is a candidate, not a library entry.
    Sequences are tuples so the model stays hashable (lists cannot be hashed).
    """

    source_id: str = Field(min_length=1)  # Google volume id; add a `source` field with Goodbooks
    title: str = Field(min_length=1)
    authors: tuple[str, ...] = ()
    isbn: str | None = None  # ISBN-10
    isbn13: str | None = None
    pages: int | None = Field(default=None, gt=0)
    language: str | None = None  # ISO 639-1 code, e.g. "en", "es"
    categories: tuple[str, ...] = ()
    description: str | None = None  # plain text, no HTML
    year: int | None = None  # original publication year when known
    cover_url: HttpUrl | None = None
    link: HttpUrl
