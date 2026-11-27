"""ProfileStore contract: what any adapter must do with temporary profiles."""

from datetime import UTC, datetime, timedelta, timezone

from book_agent.domain.profiles import StoredProfile
from tests.contracts.conftest import Stores
from tests.library_data import PROFILE

NOW = datetime(2026, 11, 27, 12, tzinfo=UTC)
TTL = timedelta(days=3)


def profile(
    profile_id: str, *, expires_at: datetime | None = NOW + TTL, **extra: object
) -> StoredProfile:
    return StoredProfile.model_validate(
        {"id": profile_id, "source": "goodreads", "profile": PROFILE, "created_at": NOW,
         "expires_at": expires_at, **extra}
    )  # fmt: skip


async def test_a_saved_profile_comes_back_unchanged(stores: Stores) -> None:
    saved = profile("p1", covers={0: "https://c/0", 3: "https://c/3"}, description="Sample")
    await stores.profiles.save(saved)
    assert await stores.profiles.get("p1", NOW) == saved


async def test_an_unknown_profile_is_none(stores: Stores) -> None:
    assert await stores.profiles.get("nope", NOW) is None


async def test_saving_again_replaces_the_profile(stores: Stores) -> None:
    await stores.profiles.save(profile("p1"))
    newer = profile("p1", covers={1: "https://c/1"}, description="Changed")
    await stores.profiles.save(newer)
    assert await stores.profiles.get("p1", NOW) == newer


async def test_an_expired_profile_is_gone_and_deleted_on_read(stores: Stores) -> None:
    await stores.profiles.save(profile("p1"))
    assert await stores.profiles.get("p1", NOW + TTL - timedelta(seconds=1)) is not None
    assert await stores.profiles.get("p1", NOW + TTL) is None  # at expires_at: expired
    # Deleted, not just hidden: reading at an earlier time doesn't bring it back.
    assert await stores.profiles.get("p1", NOW) is None


async def test_a_profile_without_expiry_never_expires(stores: Stores) -> None:
    await stores.profiles.save(profile("preset", expires_at=None))
    assert await stores.profiles.get("preset", NOW + timedelta(days=36500)) is not None


async def test_delete_says_whether_there_was_a_profile(stores: Stores) -> None:
    await stores.profiles.save(profile("p1"))
    assert await stores.profiles.delete("p1") is True
    assert await stores.profiles.delete("p1") is False
    assert await stores.profiles.get("p1", NOW) is None


async def test_purge_deletes_only_expired_profiles_and_counts_them(stores: Stores) -> None:
    await stores.profiles.save(profile("old", expires_at=NOW - timedelta(seconds=1)))
    await stores.profiles.save(profile("edge", expires_at=NOW))
    await stores.profiles.save(profile("fresh", expires_at=NOW + timedelta(seconds=1)))
    await stores.profiles.save(profile("preset", expires_at=None))
    assert await stores.profiles.purge_expired(NOW) == 2
    assert await stores.profiles.get("fresh", NOW) is not None
    assert await stores.profiles.get("preset", NOW) is not None
    assert await stores.profiles.purge_expired(NOW) == 0


async def test_expiry_compares_instants_not_clock_readings(stores: Stores) -> None:
    # The same instant written with another offset: 14:00+02:00 is 12:00 UTC.
    plus_two = timezone(timedelta(hours=2))
    expires = datetime(2026, 11, 27, 14, tzinfo=plus_two)
    await stores.profiles.save(profile("tz", expires_at=expires))
    await stores.profiles.save(profile("tz2", expires_at=expires))
    assert await stores.profiles.purge_expired(NOW - timedelta(minutes=1)) == 0
    assert await stores.profiles.get("tz", NOW - timedelta(minutes=1)) is not None
    assert await stores.profiles.purge_expired(NOW) == 2
