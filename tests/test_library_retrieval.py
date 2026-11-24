from datetime import date

import pytest

from book_agent.domain.library import retrieval_pool
from book_agent.domain.models import Book, Profile, ReadingStatus
from book_agent.embedding_index import VectorStore
from book_agent.library_enrichment import EnrichmentCache, LibraryEntry, book_key
from book_agent.library_retrieval import (
    LibraryIndex,
    document_text,
    index_library,
    library_documents,
    query_text,
)
from book_agent.ports import LibraryRetriever
from tests.fakes import FakeEmbedder

READ, TO_READ = ReadingStatus.READ, ReadingStatus.TO_READ


def book(title: str, author: str, rating: float | None, status: ReadingStatus = READ) -> Book:
    return Book(title=title, author=author, my_rating=rating, status=status)


DESERT = book("Dune", "Frank Herbert", 5.0)
SEA = book("Moby Dick", "Herman Melville", 3.0)
UNRATED = book("Ficciones", "Jorge Luis Borges", None)
DISLIKED = book("Twilight", "Stephenie Meyer", 2.0)
WANTED = book("Arrakis Notes", "Someone", None, TO_READ)
PROFILE = Profile(name="Reader", books=[UNRATED, SEA, DISLIKED, DESERT, WANTED])


def entry(b: Book, description: str | None, subjects: tuple[str, ...] = ()) -> LibraryEntry:
    return LibraryEntry(
        title=b.title, author=b.author, description=description, subjects=subjects,
        source="open_library", checked=date(2026, 11, 24),
    )  # fmt: skip


ENRICHMENT = EnrichmentCache(entries={
    book_key(DESERT): entry(DESERT, "spice desert planet sandworms " + "dune " * 200, ("desert",)),
    book_key(SEA): entry(SEA, "whale hunt at sea"),
    book_key(UNRATED): entry(UNRATED, None, ("labyrinths",)),
})  # fmt: skip


def test_the_pool_keeps_unrated_books_and_drops_low_ratings_and_unread() -> None:
    assert retrieval_pool(PROFILE) == [DESERT, SEA, UNRATED]  # liked first, unrated last


def test_document_and_query_texts_use_the_retrieval_format() -> None:
    text = document_text(DESERT, ENRICHMENT.entries[book_key(DESERT)])
    assert text.startswith("title: Dune | text: by Frank Herbert. Subjects: desert. spice desert")
    assert len(text) < 1600  # the description is cut
    assert document_text(UNRATED, None) == "title: Ficciones | text: by Jorge Luis Borges"
    assert (
        query_text("algo  del mar", "algo del mar") == "task: search result | query: algo del mar"
    )
    assert query_text("a sea story", "whales") == "task: search result | query: a sea story whales"


async def make_index(embedder: FakeEmbedder, saved: list[int] | None = None) -> LibraryIndex:
    documents = VectorStore(model=embedder.model, dimensions=embedder.dimensions)
    assert len(await index_library(PROFILE, ENRICHMENT, documents, embedder)) == 3
    queries = VectorStore(model=embedder.model, dimensions=embedder.dimensions)
    record = saved if saved is not None else []
    return LibraryIndex(
        PROFILE, ENRICHMENT, documents, queries, embedder, k=2,
        save_queries=lambda q: record.append(len(q.vectors)),
    )  # fmt: skip


async def test_the_nearest_books_come_back_with_an_excerpt() -> None:
    index = await make_index(FakeEmbedder())
    _: LibraryRetriever = index  # pyright checks the Protocol here
    related = await index.related_books(query_text("a story about whale hunt at sea", None))
    assert related[0].title == "Moby Dick" and len(related) == 2
    desert = (await index.related_books(query_text("desert planet spice", None)))[0]
    assert desert.title == "Dune" and desert.rating == 5.0
    assert desert.description is not None and desert.description.endswith("…")
    assert len(desert.description) <= 301


async def test_query_vectors_are_cached_by_text() -> None:
    embedder, saved = FakeEmbedder(), list[int]()
    index = await make_index(embedder, saved)
    query = query_text("desert planet", None)
    await index.related_books(query)
    await index.related_books(query)
    assert embedder.calls[1:] == [[query]] and saved == [1]


async def test_an_out_of_date_index_is_refused() -> None:
    embedder = FakeEmbedder()
    documents = VectorStore(model=embedder.model, dimensions=embedder.dimensions)
    await index_library(PROFILE, ENRICHMENT, documents, embedder)
    changed = EnrichmentCache(entries=ENRICHMENT.entries | {book_key(SEA): entry(SEA, "new text")})
    queries = VectorStore(model=embedder.model, dimensions=embedder.dimensions)
    with pytest.raises(ValueError, match="out of date for 1 books"):
        LibraryIndex(PROFILE, changed, documents, queries, embedder)
    with pytest.raises(ValueError, match="another embedding model"):
        LibraryIndex(PROFILE, ENRICHMENT, documents, queries, FakeEmbedder(model="other"))


def test_documents_cover_exactly_the_pool() -> None:
    assert list(library_documents(PROFILE, ENRICHMENT)) == [
        book_key(b) for b in (DESERT, SEA, UNRATED)
    ]
