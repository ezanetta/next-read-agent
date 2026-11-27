"""CoverJobStore contract: persisted, leased cover jobs that go with their profile."""

import asyncio
from datetime import UTC, datetime, timedelta

from book_agent.domain.covers import NewCoverJob
from book_agent.domain.profiles import StoredProfile
from tests.contracts.conftest import Stores
from tests.library_data import PROFILE

NOW = datetime(2026, 11, 27, 12, tzinfo=UTC)
LEASE = timedelta(minutes=2)
TTL = timedelta(days=3)


async def live_profile(
    stores: Stores, profile_id: str = "p", *, expires: datetime | None = NOW + TTL
) -> None:
    await stores.profiles.save(
        StoredProfile(
            id=profile_id, source="goodreads", profile=PROFILE, created_at=NOW, expires_at=expires
        )
    )


def jobs(*pairs: tuple[int, int]) -> list[NewCoverJob]:
    return [NewCoverJob(book_index=i, priority=p) for i, p in pairs]


async def test_enqueued_jobs_are_counted_and_duplicates_skipped(stores: Stores) -> None:
    await live_profile(stores)
    assert await stores.jobs.enqueue("p", jobs((0, 0), (1, 2), (2, 0)), NOW) == 3
    assert await stores.jobs.enqueue("p", jobs((1, 2), (3, 1)), NOW) == 1  # book 1 queued
    assert await stores.jobs.pending("p", NOW) == 4


async def test_enqueue_for_a_missing_profile_adds_nothing(stores: Stores) -> None:
    assert await stores.jobs.enqueue("ghost", jobs((0, 0)), NOW) == 0
    assert await stores.jobs.claim(NOW, LEASE) is None


async def test_claims_come_by_priority_then_age_across_profiles(stores: Stores) -> None:
    await live_profile(stores, "a")
    await live_profile(stores, "b")
    await stores.jobs.enqueue("a", jobs((0, 2), (1, 0)), NOW)
    await stores.jobs.enqueue("b", jobs((0, 0)), NOW)
    order: list[tuple[str, int]] = []
    while (job := await stores.jobs.claim(NOW, LEASE)) is not None:
        order.append((job.profile_id, job.book_index))
        assert job.attempts == 1
    assert order == [("a", 1), ("b", 0), ("a", 0)]  # read books first, then the oldest


async def test_a_leased_job_isnt_claimed_again_until_its_lease_expires(stores: Stores) -> None:
    await live_profile(stores)
    await stores.jobs.enqueue("p", jobs((0, 0)), NOW)
    first = await stores.jobs.claim(NOW, LEASE)
    assert first is not None and await stores.jobs.claim(NOW, LEASE) is None
    assert await stores.jobs.pending("p", NOW) == 1  # leased jobs are still pending
    # The process died mid-lookup: once the lease is over, the job comes back.
    again = await stores.jobs.claim(NOW + LEASE, LEASE)
    assert again is not None and again.id == first.id and again.attempts == 2


async def test_completed_jobs_are_gone(stores: Stores) -> None:
    await live_profile(stores)
    await stores.jobs.enqueue("p", jobs((0, 0), (1, 0)), NOW)
    job = await stores.jobs.claim(NOW, LEASE)
    assert job is not None
    await stores.jobs.complete(job.id)
    assert await stores.jobs.pending("p", NOW) == 1


async def test_a_failed_job_waits_until_its_retry_time(stores: Stores) -> None:
    await live_profile(stores)
    await stores.jobs.enqueue("p", jobs((0, 0)), NOW)
    job = await stores.jobs.claim(NOW, LEASE)
    assert job is not None
    retry_at = NOW + timedelta(minutes=5)
    assert await stores.jobs.fail(job.id, retry_at, max_attempts=3) is True
    assert await stores.jobs.claim(retry_at - timedelta(seconds=1), LEASE) is None
    assert await stores.jobs.pending("p", NOW) == 1
    retried = await stores.jobs.claim(retry_at, LEASE)
    assert retried is not None and retried.attempts == 2


async def test_a_job_is_given_up_after_max_attempts(stores: Stores) -> None:
    await live_profile(stores)
    await stores.jobs.enqueue("p", jobs((0, 0)), NOW)
    at = NOW
    for attempt in (1, 2, 3):
        job = await stores.jobs.claim(at, LEASE)
        assert job is not None and job.attempts == attempt
        at += timedelta(minutes=1)
        assert await stores.jobs.fail(job.id, at, max_attempts=3) is (attempt < 3)
    assert await stores.jobs.pending("p", NOW) == 0
    assert await stores.jobs.claim(at, LEASE) is None


async def test_jobs_go_with_their_profile(stores: Stores) -> None:
    await live_profile(stores)
    await stores.jobs.enqueue("p", jobs((0, 0), (1, 0)), NOW)
    await stores.profiles.delete("p")
    assert await stores.jobs.claim(NOW, LEASE) is None
    await live_profile(stores)  # the same id again: no jobs left behind
    assert await stores.jobs.pending("p", NOW) == 0


async def test_concurrent_claims_never_return_the_same_job(stores: Stores) -> None:
    await live_profile(stores)
    await stores.jobs.enqueue("p", jobs(*[(i, 0) for i in range(5)]), NOW)
    claimed = await asyncio.gather(*(stores.jobs.claim(NOW, LEASE) for _ in range(8)))
    ids = [job.id for job in claimed if job is not None]
    assert len(ids) == 5 and len(set(ids)) == 5


# --- Expiry (R-011): invisible at once, removed by purge ---------------------------------


async def test_an_expired_profiles_jobs_are_never_claimed_or_counted_before_purge(
    stores: Stores,
) -> None:
    await live_profile(stores, "old", expires=NOW + timedelta(hours=1))
    await stores.jobs.enqueue("old", jobs((0, 0), (1, 0)), NOW)
    later = NOW + timedelta(hours=1)  # expired, and nothing has purged it
    assert await stores.jobs.claim(later, LEASE) is None
    assert await stores.jobs.pending("old", later) == 0
    assert await stores.jobs.pending("old", NOW) == 2  # still there before expiry


async def test_purge_removes_an_expired_profile_with_its_covers_and_jobs(stores: Stores) -> None:
    await live_profile(stores, "old", expires=NOW + timedelta(hours=1))
    await stores.profiles.add_cover("old", 0, "https://c/0")
    await stores.jobs.enqueue("old", jobs((1, 0)), NOW)
    assert await stores.profiles.purge_expired(NOW + timedelta(hours=1)) == 1
    await live_profile(stores, "old")  # the same id again: nothing left behind
    stored = await stores.profiles.get("old", NOW)
    assert stored is not None and stored.covers == {}
    assert await stores.jobs.pending("old", NOW) == 0
