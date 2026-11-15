"""The one place where concrete adapters are built from settings.

The CLI uses these factories today; FastAPI dependencies will use the same ones.
"""

import httpx
from google import genai

from book_agent.adapters.google_books import GoogleBooksSearch
from book_agent.config import Settings
from book_agent.llm.base import LLMClient
from book_agent.llm.gemini import GeminiClient
from book_agent.ports import BookSearch


def build_llm(settings: Settings) -> LLMClient:
    """The LLM client selected by LLM_PROVIDER. Raises ValueError for unsupported providers."""
    if settings.llm_provider == "gemini":
        client = genai.Client(api_key=settings.gemini_api_key.get_secret_value())
        return GeminiClient(
            client, model=settings.gemini_model, thinking_level=settings.gemini_thinking_level
        )
    raise ValueError(f"LLM_PROVIDER={settings.llm_provider!r} is not implemented yet")


def build_book_search(settings: Settings, http: httpx.AsyncClient) -> BookSearch:
    key = settings.google_books_api_key
    return GoogleBooksSearch(http, api_key=key.get_secret_value() if key else None)
