"""Profiles as the demo stores them: where they came from, covers, and expiry (D-043)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from book_agent.domain.models import Profile

ProfileSource = Literal["manual", "goodreads", "sample", "owner"]


class StoredProfile(BaseModel, frozen=True):
    """A profile with its id. Temporary ones expire; presets (samples, owner) don't."""

    id: str = Field(min_length=1)
    source: ProfileSource
    profile: Profile
    description: str | None = None  # presets: one line for the picker
    # Cover URLs by book index: a manual list keeps its search results' covers, presets
    # bring theirs, and the cover queue adds the rest by Open Library cover id (D-046).
    covers: dict[int, str] = {}
    created_at: datetime
    expires_at: datetime | None = None  # None: never (presets)

    def expired(self, now: datetime) -> bool:
        return self.expires_at is not None and now >= self.expires_at
