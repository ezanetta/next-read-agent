"""Profiles for the demo: from a manual list, a Goodreads CSV, a sample, or the owner (D-043).

No accounts: a temporary profile's random id is its only key, and it expires after a few
days. Presets (samples and the owner's library) have fixed ids and never expire. Only the
parsed books are kept: an uploaded file is parsed in memory and never stored.
"""

import csv
import io
import json
import secrets
from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from book_agent.adapters.goodreads import parse_goodreads_csv
from book_agent.domain.models import Book, Profile, ReadingStatus
from book_agent.domain.profiles import ProfileSource, StoredProfile
from book_agent.ports import ProfileStore

MIN_MANUAL_BOOKS = 5
MAX_MANUAL_BOOKS = 50
MANUAL_RATING = 5.0  # picked books count as liked; prompts show no number (D-043)
CSV_MAX_BYTES = 1_000_000
CSV_MAX_ROWS = 2_000
MIN_READ_BOOKS = 5
DEFAULT_TTL = timedelta(days=3)
SAMPLES_DIR = Path("data_public/profiles")
OWNER_PRESET_ID = "owner"
COVER_URL = "https://covers.openlibrary.org/b/isbn/{isbn}-M.jpg?default=false"


class ProfileError(ValueError):
    """A profile that can't be made from what was sent; the message is for the user."""


class PickedBook(BaseModel, frozen=True):
    """A book the user picked from search results (the search response's fields)."""

    source_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=300)
    authors: list[str] = Field(default_factory=list[str], max_length=10)
    isbn13: str | None = Field(default=None, max_length=13)
    year: int | None = None
    cover_url: str | None = Field(default=None, max_length=500)


def new_profile_id() -> str:
    """128 random bits, URL-safe: the profile's only key."""
    return secrets.token_urlsafe(16)


def manual_profile(picks: Sequence[PickedBook]) -> tuple[Profile, dict[int, str]]:
    """A profile of picked books, all read and liked, and their covers by book index."""
    unique = list({p.source_id: p for p in picks}.values())
    if not MIN_MANUAL_BOOKS <= len(unique) <= MAX_MANUAL_BOOKS:
        raise ProfileError(
            f"pick between {MIN_MANUAL_BOOKS} and {MAX_MANUAL_BOOKS} different books "
            f"(got {len(unique)})"
        )
    books = [
        Book(
            title=p.title,
            author=p.authors[0] if p.authors else "Unknown",
            isbn13=p.isbn13,
            year=p.year,
            my_rating=MANUAL_RATING,
            status=ReadingStatus.READ,
        )
        for p in unique
    ]
    covers = {i: p.cover_url for i, p in enumerate(unique) if p.cover_url}
    return Profile(name="Your picks", books=books, ratings_implied=True), covers


def goodreads_profile(content: bytes) -> Profile:
    """A profile from an uploaded Goodreads export, within the demo's size limits."""
    if len(content) > CSV_MAX_BYTES:
        raise ProfileError(f"the file is over {CSV_MAX_BYTES // 1_000_000} MB")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise ProfileError("the file isn't UTF-8 text: is it a Goodreads CSV export?") from e
    lines = text.splitlines()
    rows = sum(1 for _ in csv.reader(io.StringIO(text))) - 1
    if rows > CSV_MAX_ROWS:
        raise ProfileError(f"the file has more than {CSV_MAX_ROWS} books")
    try:
        profile = parse_goodreads_csv(lines, profile_name="Your Goodreads library")
    except (ValueError, csv.Error) as e:
        raise ProfileError(str(e)) from e
    if len(profile.read_books) < MIN_READ_BOOKS:
        raise ProfileError(f"the library needs at least {MIN_READ_BOOKS} read books")
    return profile


def store_temporary(
    store: ProfileStore,
    source: ProfileSource,
    profile: Profile,
    now: datetime,
    *,
    covers: dict[int, str] | None = None,
    ttl: timedelta = DEFAULT_TTL,
) -> StoredProfile:
    stored = StoredProfile(
        id=new_profile_id(),
        source=source,
        profile=profile,
        covers=covers or {},
        created_at=now,
        expires_at=now + ttl,
    )
    store.save(stored)
    return stored


class PresetFile(BaseModel):
    """A sample profile file: the profile plus a line for the picker."""

    description: str
    profile: Profile


def load_presets(
    samples_dir: Path, owner_path: Path | None, now: datetime
) -> dict[str, StoredProfile]:
    """The sample profiles (`<id>.json` in `samples_dir`) and the owner's, when its file is
    configured. Missing samples are fine (none built yet); a broken file raises."""
    presets: dict[str, StoredProfile] = {}
    for path in sorted(samples_dir.glob("*.json")) if samples_dir.exists() else []:
        try:
            preset = PresetFile.model_validate_json(path.read_bytes())
        except ValidationError as e:
            raise ValueError(
                f"{path} is not a valid sample profile: {e.error_count()} errors"
            ) from e
        presets[path.stem] = StoredProfile(
            id=path.stem, source="sample", profile=preset.profile,
            description=preset.description, created_at=now,
        )  # fmt: skip
    if owner_path is not None:
        owner = Profile.model_validate_json(owner_path.read_bytes())
        presets[OWNER_PRESET_ID] = StoredProfile(
            id=OWNER_PRESET_ID, source="owner", profile=owner,
            description="The library of the person who built this demo", created_at=now,
        )  # fmt: skip
    return presets


def find_profile(
    profile_id: str, presets: dict[str, StoredProfile], store: ProfileStore, now: datetime
) -> StoredProfile | None:
    return presets.get(profile_id) or store.get(profile_id, now)


def cover_url(stored: StoredProfile, index: int) -> str | None:
    """A known cover for this book, else an Open Library Covers URL from its ISBN."""
    if index in stored.covers:
        return stored.covers[index]
    isbn = stored.profile.books[index].isbn13
    return COVER_URL.format(isbn=isbn) if isbn and isbn.isdigit() else None


def owner_export(profile: Profile) -> str:
    """The owner's profile for deploy time: only title, author, ISBNs, rating, shelf and
    year. No Goodreads id, page count, reviews or notes (the parser never reads those)."""
    data = profile.model_dump(
        mode="json",
        exclude={"ratings_implied": True, "books": {"__all__": {"goodreads_id", "pages"}}},
    )
    return json.dumps(data, ensure_ascii=False, indent=1) + "\n"
