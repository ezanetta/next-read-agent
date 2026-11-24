from pathlib import Path

import pytest

from book_agent.embedding_index import (
    StoredVector,
    VectorStore,
    cosine,
    ensure_vectors,
    load_store,
    missing,
    nearest,
    save_store,
)
from tests.fakes import FakeEmbedder


async def test_only_new_or_changed_texts_are_embedded() -> None:
    embedder = FakeEmbedder()
    store = VectorStore(model=embedder.model, dimensions=embedder.dimensions)
    assert await ensure_vectors({"a": "dune desert", "b": "sea"}, store, embedder) == ["a", "b"]
    assert await ensure_vectors({"a": "dune desert", "b": "sea"}, store, embedder) == []
    assert await ensure_vectors({"a": "dune desert", "b": "ocean"}, store, embedder) == ["b"]
    assert embedder.calls == [["dune desert", "sea"], ["ocean"]]


def test_a_store_of_another_model_starts_empty(tmp_path: Path) -> None:
    path = tmp_path / "vectors.json"
    old = VectorStore(model="old-model", dimensions=64)
    old.vectors["a"] = StoredVector(text_sha256="x", vector=[1.0])
    save_store(old, path)
    embedder = FakeEmbedder()
    assert load_store(path, embedder).vectors == {}
    assert load_store(tmp_path / "none.json", embedder).model == embedder.model
    same = load_store(path, FakeEmbedder(model="old-model"))
    assert list(same.vectors) == ["a"]


async def test_vectors_survive_a_save_and_load(tmp_path: Path) -> None:
    embedder = FakeEmbedder()
    store = VectorStore(model=embedder.model, dimensions=embedder.dimensions)
    await ensure_vectors({"a": "dune"}, store, embedder)
    save_store(store, tmp_path / "v.json")
    assert missing({"a": "dune"}, load_store(tmp_path / "v.json", embedder)) == []


async def test_ensure_refuses_a_store_of_another_model() -> None:
    with pytest.raises(ValueError, match="another model"):
        await ensure_vectors({"a": "x"}, VectorStore(model="other", dimensions=64), FakeEmbedder())


def test_cosine() -> None:
    assert cosine([1.0, 0.0], [2.0, 0.0]) == pytest.approx(1.0)
    assert cosine([1.0, 0.0], [0.0, 3.0]) == pytest.approx(0.0)
    assert cosine([0.0, 0.0], [1.0, 1.0]) == 0.0
    with pytest.raises(ValueError, match="sizes"):
        cosine([1.0], [1.0, 2.0])


def test_nearest_is_best_first_and_ties_keep_order() -> None:
    candidates = {"far": [0.0, 1.0], "tie1": [1.0, 0.0], "tie2": [2.0, 0.0], "mid": [1.0, 1.0]}
    assert [i for i, _ in nearest([1.0, 0.0], candidates, 3)] == ["tie1", "tie2", "mid"]
