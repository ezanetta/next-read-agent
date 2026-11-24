"""Which of the reader's books can be retrieved for citation, and what retrieval returns (D-041)."""

from pydantic import BaseModel, Field

from book_agent.domain.models import Book, Profile, Rating

MIN_RATING = 3.0
"""Read books rated below this are left out: the prompt lists them as "avoid similar"."""


class RelatedBook(BaseModel, frozen=True):
    """A read book retrieved as related to a request, as the agent prompt shows it."""

    title: str = Field(min_length=1)
    author: str
    rating: Rating | None
    description: str | None  # an excerpt, or None when no source described the book


def retrieval_pool(profile: Profile) -> list[Book]:
    """Read books the agent may be pointed to: rated 3 or higher, or not rated at all.

    An unrated read book is neutral, not negative, so it stays in. To-read and
    currently-reading books are out: they can't be cited as books the reader has read.
    Ordered liked first (by rating, unrated last), so similarity ties favor liked books.
    """
    pool = [b for b in profile.read_books if b.my_rating is None or b.my_rating >= MIN_RATING]
    return sorted(pool, key=lambda b: -(b.my_rating or 0.0))
