"""R-011: expired profiles are purged at startup and periodically, with covers and jobs."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from book_agent.adapters.sqlite_store import SqliteStore
from book_agent.config import Settings
from book_agent.domain.covers import NewCoverJob
from book_agent.domain.profiles import StoredProfile
from book_agent.purge import purge_once, purge_periodically
from book_agent.wiring import api_services
from tests.library_data import PROFILE

NOW = datetime(2026, 11, 27, 12, tzinfo=UTC)


async def save(store: SqliteStore, profile_id: str, expires: datetime) -> None:
    await store.save(
        StoredProfile(id=profile_id, source="goodreads", profile=PROFILE, created_at=NOW,
                      expires_at=expires, covers={0: "https://c/0"})
    )  # fmt: skip
    await store.enqueue(profile_id, [NewCoverJob(book_index=1, priority=0)], NOW)


async def test_purge_once_deletes_expired_profiles_and_logs_only_a_count(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # On the logger itself: CLI tests leave the "book_agent" logger at WARNING.
    caplog.set_level(logging.INFO, logger="book_agent.purge")
    store = SqliteStore(":memory:")
    await save(store, "old-profile-id", NOW - timedelta(seconds=1))
    await save(store, "fresh", NOW + timedelta(days=1))
    assert await purge_once(store, NOW) == 1
    assert await store.pending("old-profile-id", NOW - timedelta(days=1)) == 0  # jobs gone
    assert "purged 1 expired profiles" in caplog.text and "old-profile-id" not in caplog.text


class Ticks:
    """A sleep that returns at once and stops the loop after `limit` calls."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.slept: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.slept.append(seconds)
        if len(self.slept) > self.limit:
            raise asyncio.CancelledError
        await asyncio.sleep(0)


async def test_purge_runs_every_interval_until_cancelled() -> None:
    store = SqliteStore(":memory:")
    clock = iter([NOW, NOW + timedelta(hours=1), NOW + timedelta(hours=2)])
    await save(store, "expires-in-90-min", NOW + timedelta(minutes=90))
    ticks = Ticks(limit=3)
    with pytest.raises(asyncio.CancelledError):
        await purge_periodically(store, interval_s=3600, clock=lambda: next(clock), sleep=ticks)
    assert ticks.slept == [3600, 3600, 3600, 3600]
    assert await store.purge_expired(NOW + timedelta(days=9)) == 0  # already purged


async def test_a_failed_purge_is_logged_and_tried_again(caplog: pytest.LogCaptureFixture) -> None:
    class Flaky(SqliteStore):
        calls = 0

        async def purge_expired(self, now: datetime) -> int:
            self.calls += 1
            if self.calls == 1:
                raise ConnectionError("database briefly unreachable")
            return await super().purge_expired(now)

    store = Flaky(":memory:")
    await save(store, "old", NOW - timedelta(seconds=1))
    with pytest.raises(asyncio.CancelledError):
        await purge_periodically(store, interval_s=60, clock=lambda: NOW, sleep=Ticks(limit=2))
    assert store.calls == 2
    assert "purging expired profiles failed: ConnectionError" in caplog.text
    assert await store.purge_expired(NOW) == 0  # the second run purged it


async def test_the_api_purges_at_startup_and_stops_purging_on_shutdown(tmp_path: Path) -> None:
    path = tmp_path / "demo.sqlite3"
    store = SqliteStore(path)
    now = datetime.now(UTC)
    await save(store, "expired", now - timedelta(minutes=1))
    await save(store, "fresh", now + timedelta(days=1))
    store.close()

    settings = Settings.model_validate(
        {"gemini_api_key": "test-key", "demo_db_path": path, "purge_interval_s": 60}
    )
    async with api_services(settings) as services:  # no OPEN_LIBRARY_CONTACT: no network
        assert await services.profiles.get("expired", now - timedelta(days=1)) is None
        assert await services.profiles.get("fresh", now) is not None
    others = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    assert all(t.done() for t in others)  # the purge task stopped with the app
