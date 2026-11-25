"""Stored profiles as the API shows them."""

from book_agent.api.schemas import LibraryBook, ProfileSummary, Shelf
from book_agent.demo_profiles import cover_url
from book_agent.domain.models import ReadingStatus
from book_agent.domain.profiles import StoredProfile

_SHELVES: dict[ReadingStatus, Shelf] = {
    ReadingStatus.READ: "read",
    ReadingStatus.TO_READ: "to_read",
    ReadingStatus.CURRENTLY_READING: "currently_reading",
}


def profile_summary(stored: StoredProfile) -> ProfileSummary:
    profile = stored.profile
    return ProfileSummary(
        id=stored.id,
        name=profile.name,
        source=stored.source,
        description=stored.description,
        books=len(profile.books),
        read=len(profile.read_books),
        liked=len(profile.liked_books),
        to_read=len(profile.to_read_books),
        ratings_implied=profile.ratings_implied,
        expires_at=stored.expires_at,
    )


def library_books(stored: StoredProfile, shelf: Shelf | None = None) -> list[LibraryBook]:
    implied = stored.profile.ratings_implied
    books: list[LibraryBook] = []
    for index, book in enumerate(stored.profile.books):
        book_shelf = _SHELVES[book.status]
        if shelf is not None and book_shelf != shelf:
            continue
        books.append(
            LibraryBook(
                title=book.title,
                author=book.author,
                year=book.year,
                shelf=book_shelf,
                rating=None if implied else book.my_rating,
                picked=implied,
                cover_url=cover_url(stored, index),
            )
        )
    return books
