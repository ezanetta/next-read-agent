import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from book_agent.adapters.sqlite_store import SqliteStore
from book_agent.demo_profiles import (
    CSV_MAX_ROWS,
    DEFAULT_TTL,
    PickedBook,
    PresetFile,
    ProfileError,
    cover_url,
    find_profile,
    goodreads_profile,
    load_presets,
    manual_profile,
    new_profile_id,
    owner_export,
    store_temporary,
)
from book_agent.domain.models import Profile
from tests.library_data import PROFILE

NOW = datetime(2026, 11, 25, tzinfo=UTC)
FIXTURE = Path("tests/fixtures/goodreads_sample.csv")


def pick(i: int, cover: str | None = None) -> PickedBook:
    return PickedBook(source_id=f"id{i}", title=f"Book {i}", authors=[f"Author {i}"],
                      isbn13="9780000000002" if i == 0 else None, cover_url=cover)  # fmt: skip


def test_a_manual_list_is_read_liked_with_implied_ratings_and_keeps_covers() -> None:
    profile, covers = manual_profile(
        [pick(0, "https://c/0"), pick(1), pick(1), *map(pick, range(2, 5))]
    )
    assert len(profile.books) == 5 and profile.ratings_implied  # the duplicate counted once
    assert len(profile.liked_books) == 5 and covers == {0: "https://c/0"}


@pytest.mark.parametrize("count", [4, 51])
def test_a_manual_list_needs_5_to_50_books(count: int) -> None:
    with pytest.raises(ProfileError, match="between 5 and 50"):
        manual_profile([pick(i) for i in range(count)])


def upload(read_books: int) -> bytes:
    header = "Book Id,Title,Author,My Rating,Exclusive Shelf"
    rows = [f"{i},Book {i},Author {i},4,read" for i in range(read_books)]
    return "\n".join([header, *rows, "99,Later,Someone,0,to-read"]).encode()


def test_a_goodreads_upload_is_parsed_in_memory() -> None:
    profile = goodreads_profile(upload(5))
    assert len(profile.read_books) == 5 and len(profile.books) == 6
    assert not profile.ratings_implied


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"x" * 1_000_001, "over 1 MB"),
        (b"\xff\xfe\x00", "isn't UTF-8"),
        (b"a,b\n1,2\n", "Not a Goodreads export"),
    ],
)
def test_bad_uploads_are_refused_with_a_message(content: bytes, message: str) -> None:
    with pytest.raises(ProfileError, match=message):
        goodreads_profile(content)


def test_uploads_have_a_row_limit_and_need_read_books() -> None:
    header = FIXTURE.read_text(encoding="utf-8").splitlines()[0]
    with pytest.raises(ProfileError, match=f"more than {CSV_MAX_ROWS}"):
        goodreads_profile(("\n".join([header] + ["x"] * (CSV_MAX_ROWS + 1))).encode())
    with pytest.raises(ProfileError, match="at least 5 read books"):
        goodreads_profile(upload(4))


async def test_temporary_profiles_get_a_random_id_and_expire() -> None:
    store = SqliteStore(":memory:")
    stored = await store_temporary(store, "manual", PROFILE, NOW, covers={1: "c"})
    assert len(stored.id) >= 22 and stored.id != new_profile_id()
    assert stored.expires_at == NOW + DEFAULT_TTL
    assert await find_profile(stored.id, {}, store, NOW + timedelta(days=1)) == stored
    assert await find_profile(stored.id, {}, store, NOW + timedelta(days=4)) is None


async def test_presets_load_samples_and_the_owner(tmp_path: Path) -> None:
    samples = tmp_path / "profiles"
    samples.mkdir()
    (samples / "sample-classics.json").write_text(
        PresetFile(description="Classics", profile=PROFILE).model_dump_json()
    )
    owner = tmp_path / "owner.json"
    owner.write_text(owner_export(PROFILE, {1: "https://covers.openlibrary.org/b/id/7-M.jpg"}))
    presets = load_presets(samples, owner, NOW)
    assert sorted(presets) == ["owner", "sample-classics"]
    assert presets["owner"].source == "owner" and presets["owner"].expires_at is None
    assert presets["owner"].covers == {1: "https://covers.openlibrary.org/b/id/7-M.jpg"}
    assert load_presets(tmp_path / "none", None, NOW) == {}
    store = SqliteStore(":memory:")
    assert await find_profile("owner", presets, store, NOW) == presets["owner"]


def test_owner_files_in_the_old_plain_profile_format_ask_for_a_new_export(
    tmp_path: Path,
) -> None:
    owner = tmp_path / "owner.json"
    owner.write_text(PROFILE.model_dump_json())
    with pytest.raises(ValueError, match="export-profile"):
        load_presets(tmp_path / "none", owner, NOW)


async def test_covers_are_only_known_ones_never_by_isbn() -> None:
    # Pick 0 has an ISBN: no ISBN cover URL, Open Library rate-limits those (F-023).
    profile, covers = manual_profile([pick(i) for i in range(5)])
    stored = await store_temporary(
        SqliteStore(":memory:"), "manual", profile, NOW, covers={2: "https://c/2"}
    )
    assert cover_url(stored, 2) == "https://c/2"
    assert cover_url(stored, 0) is None and cover_url(stored, 1) is None and covers == {}


def test_the_owner_export_keeps_only_the_agreed_fields() -> None:
    data = json.loads(owner_export(PROFILE, {0: "https://covers.openlibrary.org/b/id/1-M.jpg"}))
    assert set(data) == {"description", "profile", "covers"}
    assert set(data["profile"]) == {"name", "books"}
    assert data["covers"] == {"0": "https://covers.openlibrary.org/b/id/1-M.jpg"}
    assert {k for b in data["profile"]["books"] for k in b} <= {
        "title",
        "author",
        "isbn",
        "isbn13",
        "my_rating",
        "year",
        "status",
    }
    assert Profile.model_validate(data["profile"]).books[0].title == PROFILE.books[0].title
