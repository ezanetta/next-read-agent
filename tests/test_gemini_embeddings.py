from collections.abc import Sequence
from typing import cast

import pytest
from google import genai
from google.genai import errors, types

from book_agent.llm.base import Embedder, LLMError, LLMServerError
from book_agent.llm.gemini_embeddings import (
    GeminiEmbedder,
    from_embed_response,
    to_embed_contents,
)


def response(*vectors: list[float]) -> types.EmbedContentResponse:
    return types.EmbedContentResponse(
        embeddings=[types.ContentEmbedding(values=v) for v in vectors]
    )


class _Models:
    def __init__(self, fail_with: Exception | None = None) -> None:
        self.calls: list[tuple[str, int, int | None]] = []
        self.fail_with = fail_with

    async def embed_content(
        self, *, model: str, contents: Sequence[types.Content], config: types.EmbedContentConfig
    ) -> types.EmbedContentResponse:
        self.calls.append((model, len(contents), config.output_dimensionality))
        if self.fail_with is not None:
            raise self.fail_with
        return response(*([float(i), 1.0] for i in range(len(contents))))


class _Aio:
    def __init__(self, models: _Models) -> None:
        self.models = models


class _Client:
    def __init__(self, models: _Models) -> None:
        self.aio = _Aio(models)


def embedder(models: _Models, batch_size: int = 2) -> GeminiEmbedder:
    return GeminiEmbedder(
        cast(genai.Client, _Client(models)), model="m", dimensions=8, batch_size=batch_size
    )


def test_each_text_is_its_own_content() -> None:
    contents = to_embed_contents(["a", "b"])
    assert [[p.text for p in c.parts or []] for c in contents] == [["a"], ["b"]]


def test_response_vectors_come_back_in_order() -> None:
    assert from_embed_response(response([1.0], [2.0]), expected=2) == [[1.0], [2.0]]


@pytest.mark.parametrize(
    "bad", [response([1.0]), response([1.0], []), types.EmbedContentResponse()]
)
def test_a_missing_or_empty_vector_is_an_error(bad: types.EmbedContentResponse) -> None:
    with pytest.raises(LLMError):
        from_embed_response(bad, expected=2)


async def test_texts_are_sent_in_batches_with_the_dimensions() -> None:
    models = _Models()
    client = embedder(models)
    _: Embedder = client  # pyright checks the Protocol here
    vectors = await client.embed(["a", "b", "c"])
    assert models.calls == [("m", 2, 8), ("m", 1, 8)]
    assert vectors == [[0.0, 1.0], [1.0, 1.0], [0.0, 1.0]]
    assert client.requests == 2 and (client.model, client.dimensions) == ("m", 8)


async def test_sdk_errors_map_to_llm_errors() -> None:
    error = errors.ServerError(
        503, {"error": {"code": 503, "message": "busy", "status": "UNAVAILABLE"}}
    )
    with pytest.raises(LLMServerError):
        await embedder(_Models(fail_with=error)).embed(["a"])


def test_batch_size_must_be_positive() -> None:
    with pytest.raises(ValueError, match="batch_size"):
        embedder(_Models(), batch_size=0)
