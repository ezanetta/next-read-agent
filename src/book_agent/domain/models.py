"""Domain models (Pydantic): Book and Profile.

Book describes one entry in a reader's library, normalized from any source
(Goodreads CSV export today, manual entry later).
Profile is a named reader plus their books, with helpers that split the library
into the taste signals the agent uses.
"""

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field

LIKED_MIN_RATING = 4

Rating = Annotated[int, Field(ge=1, le=5)]
"""A star rating from 1 to 5. Unrated books use None, never 0."""


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
        """Read books rated 4 or 5."""
        return [
            b
            for b in self.read_books
            if b.my_rating is not None and b.my_rating >= LIKED_MIN_RATING
        ]

    @property
    def not_so_liked_books(self) -> list[Book]:
        """Read books rated 1 to 3. Unrated books are neither liked nor not-so-liked."""
        return [
            b for b in self.read_books if b.my_rating is not None and b.my_rating < LIKED_MIN_RATING
        ]
