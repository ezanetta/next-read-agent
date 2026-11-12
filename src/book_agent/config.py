"""Application settings loaded from environment variables and the .env file."""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed configuration. Env var names match field names case-insensitively."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    llm_provider: Literal["gemini", "claude"] = "gemini"
    gemini_api_key: SecretStr
    anthropic_api_key: SecretStr | None = None
    google_books_api_key: SecretStr | None = None


@lru_cache
def get_settings() -> Settings:
    """Build Settings once and reuse it (lazy singleton)."""
    return Settings()  # pyright: ignore[reportCallIssue]  # values come from env, not args
