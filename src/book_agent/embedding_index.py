"""A small, reusable vector index: cached embeddings by id, and nearest neighbors.

Generic on purpose (D-040, D-041): the reader's library uses it now, and a catalog can use
it later. Vectors are cached on disk with the model and dimensions that produced them and
a hash of each text, so changing the model re-embeds everything and changing one text
re-embeds only that item. Nearest neighbors are brute-force cosine similarity in plain
Python: instant for hundreds of items, and a vector database can replace it when a
catalog needs one.
"""

import hashlib
import math
from collections.abc import Mapping, Sequence
from pathlib import Path

from pydantic import BaseModel

from book_agent.files import write_atomic
from book_agent.llm.base import Embedder

STORE_VERSION = 1


class StoredVector(BaseModel, frozen=True):
    text_sha256: str
    vector: list[float]


class VectorStore(BaseModel):
    """Vectors of one model and size, by item id (a book key, or a query text hash)."""

    version: int = STORE_VERSION
    model: str
    dimensions: int
    vectors: dict[str, StoredVector] = {}

    def vector(self, item_id: str, text: str) -> list[float] | None:
        """The cached vector for this id, if it was embedded from exactly this text."""
        stored = self.vectors.get(item_id)
        return (
            stored.vector if stored is not None and stored.text_sha256 == text_hash(text) else None
        )


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_store(path: Path, embedder: Embedder) -> VectorStore:
    """The store at `path`, or an empty one when it is missing or holds another model's
    vectors (they can't be compared with the new model's)."""
    empty = VectorStore(model=embedder.model, dimensions=embedder.dimensions)
    if not path.exists():
        return empty
    store = VectorStore.model_validate_json(path.read_text(encoding="utf-8"))
    if (store.model, store.dimensions) != (embedder.model, embedder.dimensions):
        return empty
    return store


def save_store(store: VectorStore, path: Path) -> None:
    write_atomic(path, store.model_dump_json() + "\n")


def missing(items: Mapping[str, str], store: VectorStore) -> list[str]:
    """Ids whose text has no cached vector (new items, or items whose text changed)."""
    return [item_id for item_id, text in items.items() if store.vector(item_id, text) is None]


async def ensure_vectors(
    items: Mapping[str, str], store: VectorStore, embedder: Embedder
) -> list[str]:
    """Embed the items that `missing` reports, store them, and return their ids.

    `items` maps id to the exact text to embed (task formatting included). Raises the
    embedder's LLMError on failure; vectors embedded before the failure stay stored.
    """
    if (store.model, store.dimensions) != (embedder.model, embedder.dimensions):
        raise ValueError("the store holds another model's vectors: load it with load_store")
    todo = missing(items, store)
    if not todo:
        return []
    vectors = await embedder.embed([items[i] for i in todo])
    for item_id, vector in zip(todo, vectors, strict=True):
        store.vectors[item_id] = StoredVector(text_sha256=text_hash(items[item_id]), vector=vector)
    return todo


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity; 0.0 when either vector is all zeros."""
    if len(a) != len(b):
        raise ValueError(f"vectors of different sizes: {len(a)} and {len(b)}")
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norms = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norms if norms else 0.0


def nearest(
    query: Sequence[float], candidates: Mapping[str, Sequence[float]], k: int
) -> list[tuple[str, float]]:
    """The `k` most similar candidate ids with their scores, best first. Ties keep the
    candidates' order, so callers decide tie-breaking by ordering the mapping."""
    scored = [(item_id, cosine(query, vector)) for item_id, vector in candidates.items()]
    scored.sort(key=lambda pair: -pair[1])  # stable: ties keep insertion order
    return scored[:k]
