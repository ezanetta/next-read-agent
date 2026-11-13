import pytest
from pydantic import ValidationError

from book_agent.domain.models import Book, Profile, ReadingStatus


def make_book(
    goodreads_id: str = "1",
    *,
    status: ReadingStatus = ReadingStatus.READ,
    my_rating: float | None = None,
) -> Book:
    return Book(
        goodreads_id=goodreads_id,
        title=f"Book {goodreads_id}",
        author="Some Author",
        my_rating=my_rating,
        status=status,
    )


@pytest.fixture
def profile() -> Profile:
    return Profile(
        name="Reader",
        books=[
            make_book("loved", my_rating=5),
            make_book("liked", my_rating=4),
            make_book("meh", my_rating=3),
            make_book("disliked", my_rating=1),
            make_book("unrated"),
            make_book("wishlist", status=ReadingStatus.TO_READ),
            make_book("rated-wishlist", status=ReadingStatus.TO_READ, my_rating=5),
            make_book("in-progress", status=ReadingStatus.CURRENTLY_READING, my_rating=2),
        ],
    )


def ids(books: list[Book]) -> list[str | None]:
    return [b.goodreads_id for b in books]


# --- Profile helpers ---------------------------------------------------------


def test_read_books(profile: Profile) -> None:
    assert ids(profile.read_books) == ["loved", "liked", "meh", "disliked", "unrated"]


def test_to_read_books(profile: Profile) -> None:
    assert ids(profile.to_read_books) == ["wishlist", "rated-wishlist"]


def test_liked_books_are_read_and_rated_4_or_5(profile: Profile) -> None:
    assert ids(profile.liked_books) == ["loved", "liked"]


def test_not_so_liked_books_are_read_and_rated_1_to_3(profile: Profile) -> None:
    assert ids(profile.not_so_liked_books) == ["meh", "disliked"]


def test_unrated_read_book_is_neither_liked_nor_not_so_liked(profile: Profile) -> None:
    assert "unrated" in ids(profile.read_books)
    assert "unrated" not in ids(profile.liked_books)
    assert "unrated" not in ids(profile.not_so_liked_books)


def test_rated_book_that_is_not_read_is_not_a_taste_signal(profile: Profile) -> None:
    taste = ids(profile.liked_books) + ids(profile.not_so_liked_books)
    assert "rated-wishlist" not in taste
    assert "in-progress" not in taste


def test_empty_profile_returns_empty_lists() -> None:
    empty = Profile(name="Nobody", books=[])
    assert empty.read_books == []
    assert empty.to_read_books == []
    assert empty.liked_books == []
    assert empty.not_so_liked_books == []


# --- Book validation ---------------------------------------------------------


@pytest.mark.parametrize(
    ("rating", "liked"),
    [(5.0, True), (4.5, True), (4.0, True), (3.75, False), (3.5, False), (0.5, False)],
)
def test_fractional_ratings_split_at_4(rating: float, liked: bool) -> None:
    profile = Profile(name="Reader", books=[make_book(my_rating=rating)])
    assert bool(profile.liked_books) is liked
    assert bool(profile.not_so_liked_books) is not liked


@pytest.mark.parametrize("rating", [0, 0.0, 5.5, 6, -1])
def test_rating_outside_0_exclusive_to_5_is_rejected(rating: float) -> None:
    with pytest.raises(ValidationError):
        make_book(my_rating=rating)


@pytest.mark.parametrize("pages", [0, -10])
def test_non_positive_pages_are_rejected(pages: int) -> None:
    with pytest.raises(ValidationError):
        Book(goodreads_id="1", title="T", author="A", pages=pages, status=ReadingStatus.READ)


def test_empty_title_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Book(goodreads_id="1", title="", author="A", status=ReadingStatus.READ)


def test_book_is_immutable() -> None:
    book = make_book()
    with pytest.raises(ValidationError):
        book.title = "Changed"  # pyright: ignore[reportAttributeAccessIssue]
