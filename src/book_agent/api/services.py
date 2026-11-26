"""What the API's routes need, built once by wiring (or by tests, with fakes)."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from book_agent.covers import CoverQueue
from book_agent.domain.profiles import StoredProfile
from book_agent.llm.base import LLMClient
from book_agent.ports import BookSearch, ProfileStore, SearchCache, WorkResolver
from book_agent.prompts.agent_prompt import AgentPrompt


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class ApiServices:
    profiles: ProfileStore
    search_cache: SearchCache
    books: BookSearch
    llm: LLMClient
    prompt: AgentPrompt
    works: WorkResolver | None = None
    covers: CoverQueue | None = None  # None without OPEN_LIBRARY_CONTACT: placeholders
    presets: dict[str, StoredProfile] = field(default_factory=dict[str, StoredProfile])
    profile_ttl: timedelta = timedelta(days=3)
    clock: Callable[[], datetime] = utc_now
