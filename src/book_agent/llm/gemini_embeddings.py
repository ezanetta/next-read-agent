"""Gemini embeddings: implements the Embedder contract on top of the google-genai SDK.

Texts are sent in batches, one Content per text, so each gets its own vector (with
gemini-embedding-2, several parts inside one Content would be aggregated into a single
vector). The translation lives in pure functions so it can be tested without network.
Errors map to the LLM error hierarchy like the chat adapter's (to_llm_error).
"""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from time import monotonic

import httpx2
from google import genai
from google.genai import errors, types

from book_agent.llm.base import LLMError, LLMNetworkError
from book_agent.llm.gemini import to_llm_error

DEFAULT_EMBEDDING_MODEL = "gemini-embedding-2"
DEFAULT_DIMENSIONS = 768  # truncated and normalized by the model; enough for ~100s of books
DEFAULT_BATCH_SIZE = 20  # texts per request: well under the free tier's 100 requests a minute


class GeminiEmbedder:
    """Embedder backed by Gemini. The genai.Client is injected and owns the API key."""

    def __init__(
        self,
        client: genai.Client,
        *,
        model: str = DEFAULT_EMBEDDING_MODEL,
        dimensions: int = DEFAULT_DIMENSIONS,
        batch_size: int = DEFAULT_BATCH_SIZE,
        min_interval_s: float = 0.0,
        timeout_s: float | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        """`min_interval_s` spaces requests out, to stay under a tokens-per-minute limit
        when embedding many texts (the library index); queries need none. `timeout_s`
        fails a request that takes longer with LLMNetworkError (R-003)."""
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        self._client = client
        self._model = model
        self._dimensions = dimensions
        self._batch_size = batch_size
        self._min_interval_s = min_interval_s
        self._timeout_s = timeout_s
        self._sleep = sleep
        self._clock = clock
        self._last_request: float | None = None
        self.requests = 0  # sent requests, for plans and reports

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            await self._wait_turn()
            self.requests += 1
            try:
                async with asyncio.timeout(self._timeout_s):
                    # The SDK's `contents` union mentions types from optional deps (PIL).
                    response = await self._client.aio.models.embed_content(  # pyright: ignore[reportUnknownMemberType]
                        model=self._model,
                        contents=to_embed_contents(batch),
                        config=types.EmbedContentConfig(output_dimensionality=self._dimensions),
                    )
            except TimeoutError as e:
                raise LLMNetworkError(
                    f"Gemini embedding timed out after {self._timeout_s:g} s"
                ) from e
            except errors.APIError as e:
                raise to_llm_error(e) from e
            except httpx2.TransportError as e:
                raise LLMNetworkError(f"Gemini embedding failed: {type(e).__name__}") from e
            except httpx2.HTTPError as e:
                raise LLMError(f"Gemini embedding failed: {type(e).__name__}") from e
            vectors += from_embed_response(response, expected=len(batch))
        return vectors

    @property
    def batch_size(self) -> int:
        return self._batch_size

    async def _wait_turn(self) -> None:
        now = self._clock()
        if self._last_request is not None:
            wait = self._last_request + self._min_interval_s - now
            if wait > 0:
                await self._sleep(wait)
                now += wait
        self._last_request = now


def to_embed_contents(texts: Sequence[str]) -> list[types.Content]:
    """One Content per text, so the model returns one vector per text."""
    return [types.Content(parts=[types.Part.from_text(text=t)]) for t in texts]


def from_embed_response(
    response: types.EmbedContentResponse, *, expected: int
) -> list[list[float]]:
    """The vectors, in order. Raises LLMError if the count or a vector is missing."""
    embeddings = response.embeddings or []
    if len(embeddings) != expected:
        raise LLMError(f"Gemini returned {len(embeddings)} embeddings for {expected} texts")
    vectors: list[list[float]] = []
    for embedding in embeddings:
        if not embedding.values:
            raise LLMError("Gemini returned an empty embedding")
        vectors.append(list(embedding.values))
    return vectors
