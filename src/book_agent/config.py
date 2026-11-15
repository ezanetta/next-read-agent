"""Application settings loaded from environment variables and the .env file."""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
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
    anthropic_api_key: SecretStr | None = None
    google_books_api_key: SecretStr | None = None


@lru_cache
def get_settings() -> Settings:
    """Build Settings once and reuse it (lazy singleton)."""
    return Settings()  # pyright: ignore[reportCallIssue]  # values come from env, not args
