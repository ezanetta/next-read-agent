"""A small synthetic reader profile and catalog shared by validation, baseline and CLI tests."""

from pydantic import HttpUrl

from book_agent.domain.models import Book, CatalogBook, Profile, ReadingStatus
from book_agent.domain.recommendations import Recommendation
from tests.fakes import FakeBookSearch

READ, TO_READ, READING = (
    ReadingStatus.READ,
    ReadingStatus.TO_READ,
    ReadingStatus.CURRENTLY_READING,
)


def book(
    title: str,
    author: str,
    status: ReadingStatus = READ,
    rating: float | None = None,
    isbn13: str | None = None,
) -> Book:
    return Book(title=title, author=author, status=status, my_rating=rating, isbn13=isbn13)


PROFILE = Profile(
    name="Reader",
    books=[
        book("Dune (Dune Chronicles, #1)", "Frank Herbert", rating=5.0, isbn13="9780441013593"),
        book("The Final Empire (Mistborn, #1)", "Brandon Sanderson", rating=4.5),
        book("Twilight (The Twilight Saga, #1)", "Stephenie Meyer", rating=2.0),
        book("Ficciones", "Jorge Luis Borges"),
        book("The Name of the Wind (The Kingkiller Chronicle, #1)", "Patrick Rothfuss", TO_READ),
        book("Project Hail Mary", "Andy Weir", READING),
    ],
)


def catalog_book(
    source_id: str,
    title: str,
    author: str,
    pages: int | None,
    language: str | None = "en",
    isbn13: str | None = None,
) -> CatalogBook:
    return CatalogBook(
        source_id=source_id,
        title=title,
        authors=(author,),
        pages=pages,
        language=language,
        isbn13=isbn13,
        link=HttpUrl(f"https://books.google.com/books?id={source_id}"),
    )


HYPERION = catalog_book("hyperion", "Hyperion", "Dan Simmons", 482)
HYPERION_SHORT = catalog_book("hyperion-short", "Hyperion", "Dan Simmons", 350)
LEFT_HAND = catalog_book("left-hand", "The Left Hand of Darkness", "Ursula K. Le Guin", 304)
FOUNDATION = catalog_book("foundation", "Foundation", "Isaac Asimov", 255)
SOLARIS = catalog_book("solaris", "Solaris", "Stanislaw Lem", None)
DUNE = catalog_book("dune", "Dune", "Frank Herbert", 896, isbn13="9780441013593")
DUNE_OMNIBUS = catalog_book(
    "dune-omnibus", "The Desert Planet Saga", "Frank Herbert", 1200, isbn13="9780441013593"
)
FINAL_EMPIRE = catalog_book("mistborn-1", "Mistborn: The Final Empire", "Brandon Sanderson", 541)
WELL_OF_ASCENSION = catalog_book(
    "mistborn-2", "Mistborn: The Well of Ascension", "Brandon Sanderson", 590
)
NAME_OF_THE_WIND = catalog_book("name-wind", "The Name of the Wind", "Patrick Rothfuss", 662)
HAIL_MARY = catalog_book("hail-mary", "Project Hail Mary", "Andy Weir", 476)
PEDRO_ES = catalog_book("pedro-es", "Pedro Páramo", "Juan Rulfo", 128, language="es")
PEDRO_EN = catalog_book("pedro-en", "Pedro Paramo", "Juan Rulfo", 124, language="en")
NO_LANGUAGE = catalog_book("no-lang", "The Invisible Cities", "Italo Calvino", 165, language=None)
BLINDNESS_PT_BR = catalog_book(
    "blindness-br", "Ensaio sobre a Cegueira", "José Saramago", 310, language="pt-BR"
)
METAMORPHOSIS = catalog_book("metamorphosis", "The Metamorphosis", "Franz Kafka", 96)

CATALOG = [
    HYPERION,
    HYPERION_SHORT,
    LEFT_HAND,
    FOUNDATION,
    SOLARIS,
    DUNE,
    DUNE_OMNIBUS,
    FINAL_EMPIRE,
    WELL_OF_ASCENSION,
    NAME_OF_THE_WIND,
    HAIL_MARY,
    PEDRO_ES,
    PEDRO_EN,
    NO_LANGUAGE,
    BLINDNESS_PT_BR,
    METAMORPHOSIS,
]


def catalog() -> FakeBookSearch:
    return FakeBookSearch(CATALOG)


def rec(title: str, author: str, reason: str = "If you loved Dune, this fits.") -> Recommendation:
    return Recommendation(title=title, author=author, reason=reason)


CLEAN_THREE = [
    rec("Hyperion", "Dan Simmons", "Layered world-building like Dune."),
    rec("The Left Hand of Darkness", "Ursula K. Le Guin", "Political depth, as in Dune."),
    rec("Foundation", "Isaac Asimov", "Big ideas, like Ficciones in a galactic frame."),
]
