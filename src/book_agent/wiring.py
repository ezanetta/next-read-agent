"""The one place where concrete adapters are built from settings.

The CLIs use these factories today; FastAPI dependencies will use the same ones.
"""

from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from google import genai

from book_agent.adapters.google_books import GoogleBooksSearch
from book_agent.adapters.open_library import OpenLibraryWorks, user_agent
from book_agent.config import Settings
from book_agent.domain.models import Profile
from book_agent.llm.base import LLMClient
from book_agent.llm.gemini import GeminiClient
from book_agent.llm.retrying import RetryingLLM
from book_agent.ports import BookSearch, WorkResolver
from book_agent.work_keys import CachedWorkResolver, WorkKeyCache, coverage, save_cache

OPEN_LIBRARY_TIMEOUT_S = 10


def build_llm(settings: Settings) -> LLMClient:
    """The LLM client every caller uses: the provider adapter with one retry on server
    errors (D-030). Raises ValueError for unsupported providers."""
    return with_retries(build_provider(settings))


def with_retries(llm: LLMClient) -> LLMClient:
    """Wrap a client in the server-error retry. Callers that add their own wrapper (the
    eval's pacing) put it inside, so the retry is paced too."""
    return RetryingLLM(llm)


def build_provider(settings: Settings) -> LLMClient:
    """The bare adapter selected by LLM_PROVIDER, with no retries (D-027)."""
    if settings.llm_provider == "gemini":
        client = genai.Client(api_key=settings.gemini_api_key.get_secret_value())
        return GeminiClient(
            client, model=settings.gemini_model, thinking_level=settings.gemini_thinking_level
        )
    raise ValueError(f"LLM_PROVIDER={settings.llm_provider!r} is not implemented yet")


def build_book_search(settings: Settings, http: httpx.AsyncClient) -> BookSearch:
    key = settings.google_books_api_key
    return GoogleBooksSearch(http, api_key=key.get_secret_value() if key else None)


def build_open_library_http(settings: Settings) -> httpx.AsyncClient | None:
    """An httpx client that identifies itself to Open Library, or None without a contact:
    no anonymous requests, so work-key matching is off until OPEN_LIBRARY_CONTACT is set."""
    contact = settings.open_library_contact
    if contact is None:
        return None
    return httpx.AsyncClient(
        headers={"User-Agent": user_agent(contact)}, timeout=OPEN_LIBRARY_TIMEOUT_S
    )


def build_work_resolver(http: httpx.AsyncClient) -> WorkResolver:
    return OpenLibraryWorks(http)


GOOGLE_BOOKS_TIMEOUT_S = 15
NO_CONTACT = "OPEN_LIBRARY_CONTACT is not set in .env"


@dataclass
class CatalogServices:
    """The catalog side of a run: book search, and work keys when configured."""

    books: BookSearch
    works: CachedWorkResolver | None
    notes: list[str] = field(default_factory=list[str])  # one-line setup warnings


@asynccontextmanager
async def catalog_services(
    settings: Settings, profile: Profile, cache: WorkKeyCache, cache_path: Path
) -> AsyncGenerator[CatalogServices]:
    """Open the HTTP clients a run needs and close them afterwards.

    Without OPEN_LIBRARY_CONTACT, work-key matching is off (a note says so). New work
    keys learned during the run are saved to the cache on exit.
    """
    async with AsyncExitStack() as stack:
        http = await stack.enter_async_context(httpx.AsyncClient(timeout=GOOGLE_BOOKS_TIMEOUT_S))
        services = CatalogServices(books=build_book_search(settings, http), works=None)
        open_library = build_open_library_http(settings)
        if open_library is None:
            services.notes.append(f"Work-key matching is off: {NO_CONTACT}.")
        else:
            await stack.enter_async_context(open_library)
            services.works = CachedWorkResolver(build_work_resolver(open_library), cache)
            library = coverage(profile, cache)
            if not library.complete:
                services.notes.append(
                    f"Work keys: {library.unchecked} library ISBNs not resolved yet; "
                    "run the works command first."
                )
        try:
            yield services
        finally:
            if services.works is not None and services.works.changed:
                save_cache(services.works.cache, cache_path)
