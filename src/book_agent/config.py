"""Application settings loaded from environment variables and the .env file."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
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
    # Deadlines (R-003): one LLM request (generation or embedding), and one whole
    # recommendation in the demo API. A run makes up to about 10 requests.
    llm_timeout_s: float = Field(default=60.0, gt=0, le=600)
    run_deadline_s: float = Field(default=180.0, gt=0, le=1800)
    anthropic_api_key: SecretStr | None = None
    google_books_api_key: SecretStr | None = None
    # Contact details for Open Library's User-Agent (an email or a project URL). Never
    # hardcoded: the repo is public. Without it, work-key matching is off.
    open_library_contact: str | None = None
    # The demo API (D-043).
    demo_db_path: Path = Path("data/demo.sqlite3")  # local development, when no DATABASE_URL
    # Postgres for the demo's stores (D-048); unset, SQLite at demo_db_path is used.
    database_url: SecretStr | None = None
    db_pool_min_size: int = Field(default=1, ge=0, le=10)
    db_pool_max_size: int = Field(default=4, ge=1, le=20)
    owner_profile_path: Path | None = None  # cli export-profile's file; never committed
    profile_ttl_days: int = Field(default=3, ge=1, le=30)
    frontend_origins: str = ""  # comma-separated origins allowed by CORS

    @field_validator("open_library_contact")
    @classmethod
    def _blank_is_none(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


@lru_cache
def get_settings() -> Settings:
    """Build Settings once and reuse it (lazy singleton)."""
    return Settings()  # pyright: ignore[reportCallIssue]  # values come from env, not args
