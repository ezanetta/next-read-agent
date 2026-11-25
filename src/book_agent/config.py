"""Application settings loaded from environment variables and the .env file."""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

GeminiThinkingLevel = Literal["minimal", "low", "medium", "high"]
"""Supported levels vary by model (gemini-3.8-flash rejects "minimal")."""


class Settings(BaseSettings):
    """Typed configuration. Env var names match field names case-insensitively."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    llm_provider: Literal["gemini", "claude"] = "gemini"
    gemini_api_key: SecretStr
    # Exact model id, never a "-latest" alias, so eval runs stay comparable.
    # Flash-Lite has the most generous free tier; 3.6/3.8 Flash are for eval comparisons.
    gemini_model: str = "gemini-3.5-flash-lite"
    gemini_thinking_level: GeminiThinkingLevel | None = None  # None = the model's default
    # Library retrieval (D-041). Changing it re-embeds the library on the next index build.
    gemini_embedding_model: str = "gemini-embedding-2"
    anthropic_api_key: SecretStr | None = None
    google_books_api_key: SecretStr | None = None
    # Contact details for Open Library's User-Agent (an email or a project URL). Never
    # hardcoded: the repo is public. Without it, work-key matching is off.
    open_library_contact: str | None = None

    @field_validator("open_library_contact")
    @classmethod
    def _blank_is_none(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


@lru_cache
def get_settings() -> Settings:
    """Build Settings once and reuse it (lazy singleton)."""
    return Settings()  # pyright: ignore[reportCallIssue]  # values come from env, not args
