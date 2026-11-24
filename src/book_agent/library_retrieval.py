"""Retrieval over the reader's library: embed the read books once, then find the ones most
related to each request (D-041).

Texts follow gemini-embedding-2's asymmetric retrieval format: documents as
"title: ... | text: ..." and queries as "task: search result | query: ...". The format is
part of the cached text, so changing it re-embeds everything.
"""

from collections.abc import Callable, Mapping
from pathlib import Path

from book_agent.domain.library import RelatedBook, retrieval_pool
from book_agent.domain.models import Book, Profile
from book_agent.embedding_index import (
    StoredVector,
    VectorStore,
    ensure_vectors,
    missing,
    nearest,
    text_hash,
)
from book_agent.library_enrichment import EnrichmentCache, LibraryEntry, book_key
from book_agent.llm.base import Embedder
from book_agent.prompts.user_content import one_line

DEFAULT_DOCUMENTS_PATH = Path("data/library_embeddings.json")
DEFAULT_QUERIES_PATH = Path("data/query_embeddings.json")
TOP_K = 8
DOCUMENT_DESCRIPTION_CHARS = 1500  # embedded: plenty for a book's gist
PROMPT_DESCRIPTION_CHARS = 300  # shown to the agent: a few lines per book


def document_text(book: Book, entry: LibraryEntry | None) -> str:
    """What gets embedded for a library book: author, subjects and description."""
    parts = [f"by {book.author}"]
    if entry is not None and entry.subjects:
        parts.append("Subjects: " + ", ".join(entry.subjects))
    if entry is not None and entry.description:
        parts.append(entry.description[:DOCUMENT_DESCRIPTION_CHARS])
    return f"title: {one_line(book.title)} | text: {one_line('. '.join(parts))}"


def query_text(request_text: str, preference: str | None) -> str:
    """The query for a request: its text, plus the parsed preference when it differs."""
    text = one_line(request_text)
    extra = one_line(preference or "")
    query = f"{text} {extra}" if extra and extra.casefold() != text.casefold() else text
    return f"task: search result | query: {query}"


def library_documents(profile: Profile, enrichment: EnrichmentCache) -> dict[str, str]:
    """Id to embedded text for every book in the retrieval pool, in pool order."""
    return {
        book_key(b): document_text(b, enrichment.entries.get(book_key(b)))
        for b in retrieval_pool(profile)
    }


async def index_library(
    profile: Profile,
    enrichment: EnrichmentCache,
    store: VectorStore,
    embedder: Embedder,
    *,
    checkpoint: Callable[[VectorStore], None] | None = None,
) -> list[str]:
    """Embed the pool books that have no vector for their current text. Returns their ids.
    `checkpoint` saves the store after each chunk, so a rate limit keeps what was done."""
    documents = library_documents(profile, enrichment)
    return await ensure_vectors(documents, store, embedder, checkpoint=checkpoint)


class LibraryIndex:
    """LibraryRetriever over the embedded pool. Query vectors are cached by text, so an
    eval round embeds each distinct request once."""

    def __init__(
        self,
        profile: Profile,
        enrichment: EnrichmentCache,
        documents: VectorStore,
        queries: VectorStore,
        embedder: Embedder,
        *,
        k: int = TOP_K,
        save_queries: Callable[[VectorStore], None] | None = None,
    ) -> None:
        texts = library_documents(profile, enrichment)
        stale = missing(texts, documents)
        if stale:
            raise ValueError(
                f"the library index is out of date for {len(stale)} books: "
                "run `uv run python -m book_agent.cli library --yes`"
            )
        for store in (documents, queries):
            if (store.model, store.dimensions) != (embedder.model, embedder.dimensions):
                raise ValueError("the stored vectors come from another embedding model")
        self._books = {book_key(b): b for b in retrieval_pool(profile)}
        self._entries: Mapping[str, LibraryEntry] = enrichment.entries
        self._vectors = {item_id: documents.vectors[item_id].vector for item_id in texts}
        self._queries = queries
        self._embedder = embedder
        self._k = k
        self._save_queries = save_queries

    async def related_books(self, query: str) -> list[RelatedBook]:
        key = text_hash(query)
        vector = self._queries.vector(key, query)
        if vector is None:
            [vector] = await self._embedder.embed([query])
            self._queries.vectors[key] = StoredVector(text_sha256=key, vector=vector)
            if self._save_queries is not None:
                self._save_queries(self._queries)
        return [self._related(item_id) for item_id, _ in nearest(vector, self._vectors, self._k)]

    def _related(self, item_id: str) -> RelatedBook:
        book = self._books[item_id]
        entry = self._entries.get(item_id)
        description = entry.description if entry is not None else None
        return RelatedBook(
            title=book.title,
            author=book.author,
            rating=book.my_rating,
            description=_excerpt(description) if description else None,
        )


def _excerpt(text: str) -> str:
    if len(text) <= PROMPT_DESCRIPTION_CHARS:
        return text
    cut = text[:PROMPT_DESCRIPTION_CHARS].rsplit(" ", 1)[0]
    return cut.rstrip(",;:.") + "…"
