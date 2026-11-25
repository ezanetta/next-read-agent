"""The one place where concrete adapters are built from settings.

The CLIs use these factories today; FastAPI dependencies will use the same ones.
"""

from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

import httpx
from google import genai

from book_agent.adapters.google_books import GoogleBooksSearch
from book_agent.adapters.open_library import OpenLibraryWorks, user_agent
from book_agent.adapters.sqlite_store import SqliteSearchCache, SqliteStore
from book_agent.agent_run import DEFAULT_AGENT_PROMPT
from book_agent.api.services import ApiServices, utc_now
from book_agent.config import Settings
from book_agent.demo_profiles import SAMPLES_DIR, load_presets
from book_agent.domain.models import Profile
from book_agent.embedding_index import load_store, save_store
from book_agent.library_enrichment import DEFAULT_ENRICHMENT_PATH, load_enrichment
from book_agent.library_retrieval import DEFAULT_DOCUMENTS_PATH, DEFAULT_QUERIES_PATH, LibraryIndex
from book_agent.llm.base import LLMClient
from book_agent.llm.gemini import GeminiClient
from book_agent.llm.gemini_embeddings import GeminiEmbedder
from book_agent.llm.retrying import RetryingLLM
from book_agent.ports import BookSearch, WorkDescriptions, WorkResolver
from book_agent.work_keys import (
    DEFAULT_CACHE_PATH,
    CachedWorkResolver,
    WorkKeyCache,
    coverage,
    load_cache,
    save_cache,
)

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


def build_work_descriptions(http: httpx.AsyncClient) -> WorkDescriptions:
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


# Indexing the library sends 20 books (~7,000 tokens) per request: one request every 20 s
# stays under the free tier's ~30,000 tokens a minute. Queries are short and unpaced.
LIBRARY_EMBEDDING_INTERVAL_S = 20.0


def build_embedder(settings: Settings, *, min_interval_s: float = 0.0) -> GeminiEmbedder:
    """The embedder for library retrieval (D-041). Gemini only, whatever LLM_PROVIDER says."""
    client = genai.Client(api_key=settings.gemini_api_key.get_secret_value())
    return GeminiEmbedder(
        client, model=settings.gemini_embedding_model, min_interval_s=min_interval_s
    )


@dataclass(frozen=True)
class LibraryPaths:
    enrichment: Path = DEFAULT_ENRICHMENT_PATH
    documents: Path = DEFAULT_DOCUMENTS_PATH
    queries: Path = DEFAULT_QUERIES_PATH


DEFAULT_LIBRARY_PATHS = LibraryPaths()


def build_library_retriever(
    settings: Settings, profile: Profile, paths: LibraryPaths = DEFAULT_LIBRARY_PATHS
) -> LibraryIndex:
    """The retriever over the reader's library, from the caches the library command builds.

    New query vectors are saved as they are embedded. Raises ValueError when the index is
    missing or out of date for this library, before any run starts.
    """
    embedder = build_embedder(settings)
    documents = load_store(paths.documents, embedder)
    queries = load_store(paths.queries, embedder)
    return LibraryIndex(
        profile,
        load_enrichment(paths.enrichment),
        documents,
        queries,
        embedder,
        save_queries=lambda store: save_store(store, paths.queries),
    )


@asynccontextmanager
async def api_services(settings: Settings) -> AsyncGenerator[ApiServices]:
    """The demo API's services (D-043): store, presets, catalog clients and the LLM.

    The work key cache is read-only here: no file is written by API requests.
    """
    store = SqliteStore(settings.demo_db_path)
    now = utc_now()
    store.purge_expired(now)
    presets = load_presets(SAMPLES_DIR, settings.owner_profile_path, now)
    async with AsyncExitStack() as stack:
        http = await stack.enter_async_context(httpx.AsyncClient(timeout=GOOGLE_BOOKS_TIMEOUT_S))
        works: WorkResolver | None = None
        open_library = build_open_library_http(settings)
        if open_library is not None:
            await stack.enter_async_context(open_library)
            works = CachedWorkResolver(
                build_work_resolver(open_library), load_cache(DEFAULT_CACHE_PATH)
            )
        try:
            yield ApiServices(
                profiles=store,
                search_cache=SqliteSearchCache(store),
                books=build_book_search(settings, http),
                llm=build_llm(settings),
                prompt=DEFAULT_AGENT_PROMPT,
                works=works,
                presets=presets,
                profile_ttl=timedelta(days=settings.profile_ttl_days),
            )
        finally:
            store.close()


def cors_origins(settings: Settings) -> list[str]:
    return [o.strip() for o in settings.frontend_origins.split(",") if o.strip()]
